import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from safeops.domain.models import Action, Ticket, digest
from safeops.persistence.models import ActionHistoryRow, OutcomeRow
from safeops.risk.features import extract
from safeops.risk.models import Features, History


async def load_history(session: AsyncSession, dataset_id: str | None) -> list[History]:
    query = (
        select(ActionHistoryRow, OutcomeRow)
        .outerjoin(
            OutcomeRow,
            (OutcomeRow.history_id == ActionHistoryRow.id) & (OutcomeRow.outcome != "unknown"),
        )
        .where(ActionHistoryRow.tool == "issue_refund")
    )
    query = (
        query.where(ActionHistoryRow.dataset_id == dataset_id)
        if dataset_id
        else query.where(ActionHistoryRow.dataset_id.is_(None))
    )
    result = []
    for history, outcome in await session.execute(query):
        result.append(
            History(
                id=history.id,
                action=Action.model_validate(history.action),
                features=Features.model_validate(history.features),
                decision=history.decision,
                arrived_at=history.arrived_at,
                observed_at=outcome.observed_at if outcome else None,
                outcome=outcome.outcome if outcome else "unknown",
                realized_loss_cents=outcome.realized_loss_cents if outcome else 0,
                review_latency_seconds=history.review_latency_seconds,
            )
        )
    return result


def synthetic_history(count: int, seed: int) -> list[History]:
    if not 10 <= count <= 500000:
        raise ValueError("history size must be 10..500000")
    rng = random.Random(seed)
    amounts = [
        200,
        500,
        1000,
        1500,
        2000,
        3000,
        4500,
        5000,
        7500,
        10000,
        20000,
        30000,
        50000,
        100000,
        150000,
    ]
    weights = [18, 20, 20, 10, 10, 7, 5, 3, 3, 2, 1, 0.5, 0.3, 0.15, 0.05]
    start = datetime(2025, 1, 1, tzinfo=UTC)
    result = []
    for i in range(count):
        amount = rng.choices(amounts, weights)[0]
        probability = (
            0.001
            if amount <= 1000
            else 0.003
            if amount <= 2000
            else 0.008
            if amount <= 5000
            else 0.04
            if amount <= 10000
            else 0.12
        )
        ticket = Ticket(
            id=f"SYN-{seed}-{i}",
            customer_id=f"CUS-SYN-{i % 5000}",
            text="synthetic structured history",
        )
        action = Action.build(
            ticket,
            "issue_refund",
            {"invoice_ref": f"INV-{i % 90000 + 10000}", "amount_cents": amount},
            "billing",
        )
        outcome = (
            "unknown"
            if rng.random() < 0.05
            else rng.choice(["incorrect", "disputed", "reversed"])
            if rng.random() < probability
            else "correct"
        )
        decision = "DENY" if amount > 100000 else "AUTO" if amount <= 1000 else "REVIEW"
        if decision == "DENY":
            outcome = "unknown"  # no counterfactual labels for unexecuted actions
        timestamp = start + timedelta(
            days=rng.randrange(60),
            hours=rng.choices(list(range(24)), [1] * 8 + [3] * 12 + [1] * 4)[0],
            seconds=rng.randrange(3600),
        )
        result.append(
            History(
                id=digest({"seed": seed, "i": i}),
                action=action,
                features=extract(action),
                decision=decision,
                arrived_at=timestamp,
                observed_at=timestamp + timedelta(days=1 + i % 7),
                outcome=outcome,
                realized_loss_cents=amount if outcome not in ("correct", "unknown") else 0,
                review_latency_seconds=rng.uniform(90, 240) if decision == "REVIEW" else None,
            )
        )
    return result
