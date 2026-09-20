from typing import Any, Literal

from pydantic import Field, model_validator
from sqlalchemy import select

from safeops.domain.models import Conflict, Forbidden, Model, NotFound, Principal, Receipt, now
from safeops.observability.redaction import redact
from safeops.persistence.database import Database
from safeops.persistence.models import EffectRow, EventRow, ReconciliationRow


class Resolution(Model):
    outcome: Literal["performed", "not_performed"]
    evidence: str = Field(min_length=5, max_length=2000)
    result: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_receipt(self) -> "Resolution":
        if self.outcome == "performed" and not self.result:
            raise ValueError("performed requires a downstream receipt")
        return self


async def reconcile(db: Database, key: str, resolution: Resolution, principal: Principal) -> str:
    if principal.role != "admin":
        raise Forbidden("reconciliation requires admin")
    async with db.sessions() as session:
        row = await session.get(EffectRow, key)
        if row is None:
            raise NotFound("effect not found")
        run_id = row.run_id
    # Same lock as runtime: never release a live in-flight operation.
    async with db.run_lock(run_id), db.sessions.begin() as session:
        row = await session.scalar(
            select(EffectRow).where(EffectRow.idempotency_key == key).with_for_update()
        )
        assert row is not None
        if row.status not in ("in_doubt", "pending"):
            raise Conflict("effect is not awaiting reconciliation")
        if resolution.outcome == "not_performed" and row.execution_count:
            raise Conflict("recorded execution contradicts not_performed")
        if resolution.outcome == "performed":
            receipt = Receipt(
                action_id=row.action_id,
                idempotency_key=key,
                tool=row.tool,
                result=redact(resolution.result or {}),
                performed_at=now(),
            )
            row.status, row.receipt = "completed", receipt.model_dump(mode="json")
        else:
            row.status = "released"
        row.updated_at = now()
        session.add(
            ReconciliationRow(
                effect_id=key,
                outcome=resolution.outcome,
                resolver=principal.name,
                evidence=redact(resolution.evidence),
                result=redact(resolution.result),
            )
        )
        session.add(
            EventRow(
                run_id=run_id,
                thread_id=run_id,
                node="reconcile",
                event_type="effect_reconciled",
                payload={
                    "effect_id": key,
                    "outcome": resolution.outcome,
                    "resolver": principal.name,
                },
            )
        )
    return run_id
