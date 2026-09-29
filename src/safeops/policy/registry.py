from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from safeops.domain.models import Action, Forbidden, digest
from safeops.persistence.database import Database
from safeops.persistence.models import (
    CalibrationRow,
    CandidateRow,
    ControlStateRow,
    EventRow,
    PolicyRevisionRow,
    ReleaseRow,
    SLORow,
)
from safeops.policy.engine import PolicyEngine
from safeops.policy.models import PolicyDecision, PolicyDefinition
from safeops.risk.models import BusinessSLO, Calibration
from safeops.tools.registry import ToolRegistry

CONTROL_LOCK = 71926001


async def control_lock(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CONTROL_LOCK})


def control_event(session: AsyncSession, event: str, payload: dict[str, Any]) -> None:
    session.add(
        EventRow(
            run_id=None,
            thread_id="control",
            node="control_plane",
            event_type=event,
            payload=payload,
        )
    )


def bootstrap_definition() -> PolicyDefinition:
    slo = BusinessSLO()
    epoch = datetime(2000, 1, 1, tzinfo=UTC)
    calibration = Calibration(
        data_digest=digest([]),
        window_start=epoch,
        window_end=epoch,
        split_at=epoch,
        source="bootstrap",
    )
    return PolicyDefinition(
        revision="bootstrap-conservative",
        slo=slo,
        calibration=calibration,
        auto_limits=(0, 0, 0),
        review_limits=(slo.hard_deny_amount_cents,) * 3,
    )


async def bootstrap(db: Database) -> None:
    async with db.sessions.begin() as session:
        await control_lock(session)
        if await session.get(ControlStateRow, 1):
            return
        policy = bootstrap_definition()
        slo = policy.slo.model_dump(mode="json")
        cal = policy.calibration.model_dump(mode="json")
        sid, cid = digest(slo), digest(cal)
        session.add(SLORow(id=sid, definition=slo, author="bootstrap"))
        session.add(
            CalibrationRow(id=cid, definition=cal, source="bootstrap", data_epoch=0, sample_count=0)
        )
        await session.flush()
        session.add(
            PolicyRevisionRow(
                id=policy.revision,
                slo_id=sid,
                calibration_id=cid,
                definition=policy.model_dump(mode="json"),
                digest=policy.digest,
            )
        )
        await session.flush()
        session.add(
            ReleaseRow(
                id=policy.revision,
                revision_id=policy.revision,
                principal="bootstrap",
                note="Explicit initialization: all uncalibrated writes require review",
                fingerprint=digest("bootstrap"),
                diff_digest=digest({}),
                slo_digest=sid,
                calibration_digest=cid,
            )
        )
        session.add(ControlStateRow(id=1, slo_id=sid, revision_id=policy.revision, data_epoch=0))
        control_event(
            session, "policy_activation_changed", {"revision": policy.revision, "bootstrap": True}
        )


class PolicyRegistry:
    def __init__(self, db: Database, tools: ToolRegistry):
        self.db, self.tools = db, tools

    async def active(self) -> PolicyDefinition:
        async with self.db.sessions() as session:
            state = await session.get(ControlStateRow, 1)
            if state is None:
                raise Forbidden("no active policy; run explicit bootstrap")
            row = await session.get(PolicyRevisionRow, state.revision_id)
            if row is None:
                raise Forbidden("active policy missing")
            release = await session.scalar(
                select(ReleaseRow).where(ReleaseRow.revision_id == row.id)
            )
            slo = await session.get(SLORow, row.slo_id)
            calibration = await session.get(CalibrationRow, row.calibration_id)
            if not release or not slo or not calibration:
                raise Forbidden("policy release or snapshots missing")
            try:
                policy = PolicyDefinition.model_validate(row.definition)
            except ValueError:
                raise Forbidden("invalid active policy") from None
            if (
                policy.revision != row.id
                or policy.digest != row.digest
                or digest(slo.definition) != row.slo_id
                or digest(calibration.definition) != row.calibration_id
                or release.slo_digest != row.slo_id
                or release.calibration_digest != row.calibration_id
                or policy.slo.model_dump(mode="json") != slo.definition
                or policy.calibration.model_dump(mode="json") != calibration.definition
            ):
                raise Forbidden("corrupted policy digest")
            if row.candidate_id:
                candidate = await session.get(CandidateRow, row.candidate_id)
                if (
                    not candidate
                    or not candidate.feasible
                    or candidate.id != row.id
                    or digest(candidate.result) != candidate.result_digest
                    or candidate.result.get("policy") != row.definition
                    or digest(candidate.result.get("replay")) != release.diff_digest
                ):
                    raise Forbidden("policy release mismatch")
            from safeops.policy.compiler import validate

            if validate(policy, policy.slo):
                raise Forbidden("invalid active policy invariants")
            return policy

    async def assess(self, action: Action) -> PolicyDecision:
        return PolicyEngine(await self.active(), self.tools).assess(action)

    @asynccontextmanager
    async def execution_guard(self) -> AsyncIterator[None]:
        # Release takes the exclusive counterpart; it cannot race a checked dispatch.
        async with self.db.sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": CONTROL_LOCK}
            )
            yield
