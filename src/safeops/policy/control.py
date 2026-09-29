import asyncio
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select

from safeops.domain.models import Conflict, Forbidden, Model, NotFound, Principal, digest
from safeops.observability.redaction import redact_text
from safeops.persistence.database import Database, row_dict
from safeops.persistence.models import (
    CalibrationRow,
    CandidateRow,
    ControlStateRow,
    PolicyRevisionRow,
    ReleaseRow,
    SLORow,
)
from safeops.policy.compiler import validate
from safeops.policy.models import CandidateResult
from safeops.policy.registry import PolicyRegistry, control_event, control_lock
from safeops.risk.calibration import calibrate
from safeops.risk.datasets import generate_dataset
from safeops.risk.history import load_history
from safeops.risk.metrics import runtime_metrics
from safeops.risk.models import BusinessSLO
from safeops.risk.optimizer import OPTIMIZER_CONFIG, optimize, recommended
from safeops.tools.registry import ToolRegistry


class CompileInput(Model):
    source: Literal["runtime", "synthetic"] = "runtime"
    dataset_id: str | None = None


class ReleaseInput(Model):
    note: str = Field(min_length=5, max_length=2000)
    diff_digest: str
    expected_revision: str


class ControlDocument(Model):
    data: dict[str, Any]


def require_admin(principal: Principal) -> None:
    if principal.role != "admin":
        raise Forbidden("control plane mutation requires admin")


class ControlPlane:
    def __init__(self, db: Database):
        self.db = db
        self.registry = PolicyRegistry(db, ToolRegistry())

    async def slo(self) -> dict[str, Any]:
        async with self.db.sessions() as session:
            state = await session.get(ControlStateRow, 1)
            if not state:
                raise Forbidden("no control plane bootstrap")
            row = await session.get(SLORow, state.slo_id)
            assert row is not None
            return row_dict(row)

    async def put_slo(self, value: BusinessSLO, principal: Principal) -> dict[str, Any]:
        require_admin(principal)
        data = value.model_dump(mode="json")
        identity = digest(data)
        async with self.db.sessions.begin() as session:
            await control_lock(session)
            state = await session.get(ControlStateRow, 1)
            if not state:
                raise Forbidden("no control plane bootstrap")
            if not await session.get(SLORow, identity):
                session.add(SLORow(id=identity, definition=data, author=principal.name))
                await session.flush()
            state.slo_id = identity
            control_event(
                session, "business_slo_updated", {"slo_id": identity, "principal": principal.name}
            )
        return await self.slo()

    async def generate(self, count: int, seed: int, principal: Principal) -> dict[str, Any]:
        return await generate_dataset(self.db, count, seed, principal)

    async def compile(self, request: CompileInput, principal: Principal) -> dict[str, Any]:
        require_admin(principal)
        if (request.source == "synthetic") != bool(request.dataset_id):
            raise Conflict("source requires matching dataset_id")
        async with self.db.sessions.begin() as session:
            await control_lock(session)
            state = await session.get(ControlStateRow, 1)
            assert state is not None
            epoch, base, sid = state.data_epoch, state.revision_id, state.slo_id
            slo_row = await session.get(SLORow, sid)
            assert slo_row is not None
            slo = BusinessSLO.model_validate(slo_row.definition)
            current = await self.registry.active()
            rows = await load_history(session, request.dataset_id)
        if len(rows) < 10:
            raise Conflict("at least 10 historical refund actions required")
        calibration, holdout = await asyncio.to_thread(calibrate, rows, request.source)
        candidates = await asyncio.to_thread(optimize, current, slo, calibration, holdout, epoch)
        cid = digest(calibration.model_dump(mode="json"))
        async with self.db.sessions.begin() as session:
            await control_lock(session)
            state = await session.get(ControlStateRow, 1)
            assert state is not None
            if (epoch, base, sid) != (state.data_epoch, state.revision_id, state.slo_id):
                raise Conflict("data/SLO/policy changed during compile; retry")
            if not await session.get(CalibrationRow, cid):
                session.add(
                    CalibrationRow(
                        id=cid,
                        definition=calibration.model_dump(mode="json"),
                        source=request.source,
                        data_epoch=epoch,
                        sample_count=calibration.history_count,
                        dataset_id=request.dataset_id,
                    )
                )
                await session.flush()
            existing = set(
                await session.scalars(
                    select(CandidateRow.id).where(CandidateRow.id.in_([c.id for c in candidates]))
                )
            )
            for c in candidates:
                if c.id in existing:
                    continue
                payload = c.model_dump(mode="json")
                session.add(
                    CandidateRow(
                        id=c.id,
                        slo_id=sid,
                        calibration_id=cid,
                        base_revision=base,
                        source=request.source,
                        data_epoch=epoch,
                        feasible=c.feasible,
                        frontier=c.frontier,
                        result=payload,
                        result_digest=digest(payload),
                        optimizer=OPTIMIZER_CONFIG,
                    )
                )
            control_event(
                session,
                "risk_calibration_completed",
                {
                    "calibration_id": cid,
                    "known": calibration.known_count,
                    "samples": calibration.history_count,
                },
            )
            control_event(
                session,
                "policy_shadow_replay_completed",
                {"samples": len(holdout), "calibration_id": cid},
            )
            control_event(
                session,
                "policy_candidate_compiled",
                {
                    "count": len(candidates),
                    "feasible": sum(c.feasible for c in candidates),
                    "calibration_id": cid,
                },
            )
        return {
            "history_count": len(rows),
            "training_count": calibration.history_count,
            "holdout_count": len(holdout),
            "known_outcome_coverage": sum(r.outcome != "unknown" for r in rows) / len(rows),
            "calibration_id": cid,
            "candidate_count": len(candidates),
            "feasible_count": sum(c.feasible for c in candidates),
            "recommended": recommended(candidates),
            "candidate_ids": [c.id for c in candidates],
            "risk_budget_per_day_cents": slo.max_expected_loss_per_day_cents,
        }

    async def candidate(self, identity: str) -> dict[str, Any]:
        async with self.db.sessions() as session:
            row = await session.get(CandidateRow, identity)
            if not row:
                raise NotFound("candidate not found")
            if digest(row.result) != row.result_digest:
                raise Forbidden("candidate digest corrupted")
            result = row_dict(row)
            result["diff_digest"] = digest(row.result["replay"])
            return result

    async def release(
        self, identity: str, body: ReleaseInput, principal: Principal
    ) -> dict[str, Any]:
        require_admin(principal)
        fingerprint = digest(
            {"id": identity, "request": body.model_dump(), "principal": principal.model_dump()}
        )
        async with self.db.sessions.begin() as session:
            await control_lock(session)
            state = await session.get(ControlStateRow, 1)
            assert state is not None
            previous = await session.get(ReleaseRow, identity)
            if previous:
                if previous.fingerprint != fingerprint or state.revision_id != identity:
                    raise Conflict("conflicting or superseded release")
                return {"revision": identity, "replay": True}
            row = await session.get(CandidateRow, identity)
            if not row:
                raise NotFound("candidate not found")
            if digest(row.result) != row.result_digest:
                raise Forbidden("candidate digest corrupted")
            candidate = CandidateResult.model_validate(row.result)
            slo_row = await session.get(SLORow, state.slo_id)
            assert slo_row is not None
            if (
                candidate.policy.revision != identity
                or candidate.policy.slo.model_dump(mode="json") != slo_row.definition
            ):
                raise Conflict("candidate SLO/revision mismatch")
            if (
                row.base_revision != state.revision_id
                or body.expected_revision != state.revision_id
                or row.slo_id != state.slo_id
                or row.data_epoch != state.data_epoch
            ):
                raise Conflict("stale candidate: active policy, SLO or observations changed")
            if body.diff_digest != digest(candidate.replay.model_dump(mode="json")):
                raise Conflict("policy diff digest mismatch")
            errors = validate(candidate.policy, BusinessSLO.model_validate(slo_row.definition))
            if not candidate.feasible or errors or candidate.replay.candidate.errors:
                raise Conflict("candidate validation failed")
            calibration = await session.get(CalibrationRow, row.calibration_id)
            if not calibration or digest(calibration.definition) != row.calibration_id:
                raise Forbidden("calibration corrupted")
            if candidate.policy.calibration.model_dump(mode="json") != calibration.definition:
                raise Forbidden("calibration mismatch")
            control_event(
                session,
                "policy_release_requested",
                {"candidate": identity, "principal": principal.name},
            )
            session.add(
                PolicyRevisionRow(
                    id=identity,
                    slo_id=row.slo_id,
                    calibration_id=row.calibration_id,
                    candidate_id=identity,
                    definition=candidate.policy.model_dump(mode="json"),
                    digest=candidate.policy.digest,
                )
            )
            await session.flush()
            session.add(
                ReleaseRow(
                    id=identity,
                    revision_id=identity,
                    principal=principal.name,
                    note=redact_text(body.note),
                    fingerprint=fingerprint,
                    diff_digest=body.diff_digest,
                    slo_digest=row.slo_id,
                    calibration_digest=row.calibration_id,
                )
            )
            state.revision_id = identity
            control_event(
                session,
                "policy_released",
                {
                    "revision": identity,
                    "principal": principal.name,
                    "diff_digest": body.diff_digest,
                },
            )
            control_event(
                session,
                "policy_activation_changed",
                {"previous": row.base_revision, "active": identity},
            )
        return {"revision": identity, "replay": False}

    async def metrics(self) -> dict[str, Any]:
        return await runtime_metrics(self.db, self.registry)
