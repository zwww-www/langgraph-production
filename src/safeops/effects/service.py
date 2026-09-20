from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from safeops.domain.models import Action, EffectInDoubt, Receipt, ToolRefused, now
from safeops.faults import Faults
from safeops.observability.redaction import redact
from safeops.persistence.database import Database
from safeops.persistence.models import EffectRow, ExecutionRow
from safeops.tools.registry import ToolRegistry


class Effects:
    def __init__(self, db: Database, registry: ToolRegistry, faults: Faults):
        self.db, self.registry, self.faults = db, registry, faults

    async def perform(
        self, run_id: str, customer: str, action: Action, dry_run: bool = False
    ) -> Receipt:
        spec = self.registry.require(action.tool, action.domain)
        args = spec.validate(action.args)
        key = f"{run_id}:{action.action_id}"
        if dry_run:
            return Receipt(
                action_id=action.action_id,
                idempotency_key=key,
                tool=action.tool,
                result={"simulated": True, "tool": action.tool, "args": args},
                performed_at=now(),
                dry_run=True,
            )
        if spec.side_effect_type == "none":
            result = await spec.handler(args, key, customer)
            return Receipt(
                action_id=action.action_id,
                idempotency_key=key,
                tool=action.tool,
                result=redact(result),
                performed_at=now(),
            )
        doubt = False
        async with self.db.sessions.begin() as session:
            inserted = await session.scalar(
                insert(EffectRow)
                .values(
                    idempotency_key=key,
                    action_id=action.action_id,
                    run_id=run_id,
                    tool=action.tool,
                    args=args,
                    status="pending",
                )
                .on_conflict_do_nothing()
                .returning(EffectRow.idempotency_key)
            )
            row = await session.scalar(
                select(EffectRow).where(EffectRow.idempotency_key == key).with_for_update()
            )
            assert row is not None
            if not inserted:
                if row.status == "completed":
                    return Receipt.model_validate(row.receipt).model_copy(update={"replayed": True})
                if row.status == "failed":
                    raise ToolRefused(row.error or "previous attempt refused")
                if row.status == "released":
                    row.status, row.attempt = "pending", row.attempt + 1
                    row.updated_at = now()
                else:
                    row.status, row.updated_at = "in_doubt", now()
                    doubt = True
            attempt = row.attempt
        if doubt:
            raise EffectInDoubt(key)
        self.faults.hit("effect_claimed")
        try:
            result = redact(await spec.handler(args, key, customer))
        except ToolRefused as exc:
            await self._status(key, "failed", str(exc))
            raise
        except Exception as exc:
            await self._status(key, "in_doubt", type(exc).__name__)
            raise EffectInDoubt(key) from exc
        self.faults.hit("external_effect_executed")
        async with self.db.sessions.begin() as session:
            session.add(ExecutionRow(effect_id=key, attempt=attempt, result=result))
            row = await session.get(EffectRow, key)
            assert row is not None
            row.execution_count += 1
        self.faults.hit("effect_recorded")
        receipt = Receipt(
            action_id=action.action_id,
            idempotency_key=key,
            tool=action.tool,
            result=result,
            performed_at=now(),
        )
        async with self.db.sessions.begin() as session:
            row = await session.get(EffectRow, key)
            assert row is not None
            row.status, row.receipt, row.updated_at = (
                "completed",
                receipt.model_dump(mode="json"),
                now(),
            )
        return receipt

    async def _status(self, key: str, status: str, error: str) -> None:
        async with self.db.sessions.begin() as session:
            row = await session.get(EffectRow, key)
            assert row is not None
            row.status, row.error, row.updated_at = status, error, now()
