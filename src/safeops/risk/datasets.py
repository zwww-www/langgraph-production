import asyncio
from typing import Any

from sqlalchemy import insert

from safeops.domain.models import Conflict, Forbidden, Principal, digest
from safeops.persistence.database import Database, row_dict
from safeops.persistence.models import ActionHistoryRow, ControlStateRow, DatasetRow, OutcomeRow
from safeops.policy.registry import control_event, control_lock
from safeops.risk.history import synthetic_history


async def generate_dataset(
    db: Database, count: int, seed: int, principal: Principal
) -> dict[str, Any]:
    if principal.role != "admin":
        raise Forbidden("history generation requires admin")
    rows = await asyncio.to_thread(synthetic_history, count, seed)
    identity = digest({"generator": "structured-refund-history", "count": count, "seed": seed})
    fingerprint = digest([r.model_dump(mode="json") for r in rows])
    async with db.sessions.begin() as session:
        await control_lock(session)
        existing = await session.get(DatasetRow, identity)
        if existing:
            if existing.digest != fingerprint:
                raise Conflict("synthetic generator content changed")
            return row_dict(existing)
        dataset = DatasetRow(id=identity, seed=seed, count=count, digest=fingerprint)
        session.add(dataset)
        await session.flush()
        for offset in range(0, len(rows), 2000):
            batch = rows[offset : offset + 2000]
            await session.execute(
                insert(ActionHistoryRow),
                [
                    {
                        "id": f"{identity[:16]}:{r.id}",
                        "action_id": r.action.action_id,
                        "dataset_id": identity,
                        "customer_id": f"CUS-SYN-{(offset + i) % 5000}",
                        "tool": r.action.tool,
                        "action": r.action.model_dump(),
                        "features": r.features.model_dump(mode="json"),
                        "decision": r.decision,
                        "expected_loss_cents": 0,
                        "review_latency_seconds": r.review_latency_seconds,
                        "arrived_at": r.arrived_at,
                    }
                    for i, r in enumerate(batch)
                ],
            )
            await session.execute(
                insert(OutcomeRow),
                [
                    {
                        "id": digest({"dataset": identity, "observation": r.id}),
                        "history_id": f"{identity[:16]}:{r.id}",
                        "outcome": r.outcome,
                        "realized_loss_cents": r.realized_loss_cents,
                        "source": "synthetic",
                        "evidence": {"seed": seed},
                        "fingerprint": digest(r.model_dump(mode="json")),
                        "observer": "synthetic-generator",
                        "observed_at": r.observed_at,
                    }
                    for r in batch
                ],
            )
        state = await session.get(ControlStateRow, 1)
        assert state is not None
        state.data_epoch += 1
        control_event(
            session, "synthetic_history_generated", {"dataset_id": identity, "count": count}
        )
    return {"id": identity, "count": count, "seed": seed, "digest": fingerprint}
