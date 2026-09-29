from sqlalchemy import select

from safeops.domain.models import (
    Action,
    Conflict,
    Forbidden,
    NotFound,
    Principal,
    Ticket,
    digest,
    now,
)
from safeops.observability.redaction import redact
from safeops.persistence.database import Database
from safeops.persistence.models import (
    ActionHistoryRow,
    ControlStateRow,
    EffectRow,
    OutcomeRow,
    RunRow,
)
from safeops.policy.models import PolicyDecision
from safeops.policy.registry import control_event, control_lock
from safeops.risk.features import extract
from safeops.risk.models import Observation


async def record_action(
    db: Database, run_id: str, ticket: Ticket, action: Action, policy: PolicyDecision
) -> None:
    key = f"{run_id}:{action.action_id}"
    async with db.sessions.begin() as session:
        await control_lock(session)
        existing = await session.get(ActionHistoryRow, key)
        if existing:
            if existing.action != action.model_dump():
                raise Conflict("historical action conflict")
            return
        run = await session.get(RunRow, run_id)
        assert run is not None
        session.add(
            ActionHistoryRow(
                id=key,
                run_id=run_id,
                action_id=action.action_id,
                customer_id=ticket.customer_id,
                tool=action.tool,
                action=action.model_dump(),
                features=extract(action).model_dump(mode="json"),
                decision={"ALLOW": "AUTO", "REQUIRE_APPROVAL": "REVIEW", "DENY": "DENY"}[
                    policy.decision
                ],
                revision_id=policy.version,
                expected_loss_cents=policy.risk.profile.expected_loss_cents
                if policy.risk.profile
                else 0,
                arrived_at=run.created_at,
            )
        )
        state = await session.get(ControlStateRow, 1)
        assert state is not None
        state.data_epoch += 1


async def observe(db: Database, observation: Observation, principal: Principal) -> dict:
    if principal.role not in ("admin", "finance"):
        raise Forbidden("business outcomes require finance/admin")
    key = f"{observation.run_id}:{observation.action_id}"
    fingerprint = digest(
        {"observation": observation.model_dump(mode="json"), "principal": principal.model_dump()}
    )
    async with db.sessions.begin() as session:
        await control_lock(session)
        previous = await session.get(OutcomeRow, observation.observation_id)
        if previous:
            if previous.fingerprint != fingerprint:
                raise Conflict("conflicting observation replay")
            return {"id": previous.id, "outcome": previous.outcome, "replay": True}
        history = await session.get(ActionHistoryRow, key)
        if not history or history.run_id != observation.run_id:
            raise NotFound("canonical action not found")
        if not history.receipt:
            effect = await session.get(EffectRow, key)
            if not effect or effect.status != "completed":
                raise Conflict("outcome requires executed action receipt")
        if observation.observed_at < history.arrived_at or observation.observed_at > now():
            raise Conflict("observation time outside action/present interval")
        final = await session.scalar(
            select(OutcomeRow).where(OutcomeRow.history_id == key, OutcomeRow.outcome != "unknown")
        )
        if final:
            raise Conflict("final business outcome already recorded; no silent overwrite")
        session.add(
            OutcomeRow(
                id=observation.observation_id,
                history_id=key,
                outcome=observation.outcome,
                realized_loss_cents=observation.realized_loss_cents,
                source=observation.source,
                evidence=redact(observation.evidence),
                fingerprint=fingerprint,
                observer=principal.name,
                observed_at=observation.observed_at,
            )
        )
        state = await session.get(ControlStateRow, 1)
        assert state is not None
        state.data_epoch += 1
        control_event(
            session,
            "business_outcome_recorded",
            {
                "history_id": key,
                "outcome": observation.outcome,
                "source": observation.source,
                "observer": principal.name,
            },
        )
    return {"id": observation.observation_id, "outcome": observation.outcome, "replay": False}
