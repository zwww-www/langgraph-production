from typing import Any

from sqlalchemy import func, select, text

from safeops.persistence.database import Database
from safeops.persistence.models import ActionHistoryRow, ApprovalRow, CandidateRow, OutcomeRow
from safeops.policy.registry import PolicyRegistry


async def runtime_metrics(db: Database, registry: PolicyRegistry) -> dict[str, Any]:
    active = await registry.active()
    async with db.sessions() as session:
        real = ActionHistoryRow.dataset_id.is_(None)
        decisions = {
            decision: count
            for decision, count in (
                await session.execute(
                    select(ActionHistoryRow.decision, func.count())
                    .where(real)
                    .group_by(ActionHistoryRow.decision)
                )
            ).all()
        }
        n = sum(decisions.values())
        outcomes = (
            await session.execute(
                select(func.count(), func.sum(OutcomeRow.realized_loss_cents))
                .join(ActionHistoryRow, OutcomeRow.history_id == ActionHistoryRow.id)
                .where(real, OutcomeRow.outcome != "unknown")
            )
        ).one()
        bad = await session.scalar(
            select(func.count())
            .select_from(OutcomeRow)
            .join(ActionHistoryRow, OutcomeRow.history_id == ActionHistoryRow.id)
            .where(real, OutcomeRow.outcome.not_in(["unknown", "correct"]))
        )
        queue = await session.scalar(
            select(func.count()).select_from(ApprovalRow).where(ApprovalRow.decision.is_(None))
        )
        consumption = await session.scalar(
            select(func.sum(ActionHistoryRow.expected_loss_cents)).where(
                real,
                ActionHistoryRow.decision == "AUTO",
                ActionHistoryRow.arrived_at >= func.now() - text_interval_day(),
            )
        )
        rolling = await session.scalar(
            select(func.sum(ActionHistoryRow.expected_loss_cents)).where(
                real,
                ActionHistoryRow.decision == "AUTO",
                ActionHistoryRow.arrived_at
                >= func.now() - text(f"INTERVAL '{active.slo.rolling_window_days} days'"),
            )
        )
        review_arrivals = await session.scalar(
            select(func.count())
            .select_from(ActionHistoryRow)
            .where(
                real,
                ActionHistoryRow.tool == "issue_refund",
                ActionHistoryRow.decision == "REVIEW",
                ActionHistoryRow.arrived_at >= func.now() - text("INTERVAL '1 hour'"),
            )
        )
        rejected = await session.scalars(
            select(CandidateRow)
            .where(CandidateRow.feasible.is_(False))
            .order_by(CandidateRow.created_at.desc())
            .limit(30)
        )
        rejections = {r.id: r.result["errors"] for r in rejected}
    throughput = next(c.throughput for c in active.slo.capacities if c.role == "finance")
    loss = consumption or 0
    return {
        "policy_revision": active.revision,
        "action_count": n,
        "auto_decision_rate": decisions.get("AUTO", 0) / max(1, n),
        "review_decision_rate": decisions.get("REVIEW", 0) / max(1, n),
        "deny_decision_rate": decisions.get("DENY", 0) / max(1, n),
        "expected_loss_consumption_cents": loss,
        "risk_budget_utilization": loss / max(1, active.slo.max_expected_loss_per_day_cents),
        "review_queue_depth": queue,
        "review_capacity_utilization": (review_arrivals or 0) / throughput if throughput else None,
        "review_capacity_scope": "finance refund arrivals during last hour / configured throughput",
        "rolling_expected_loss_consumption_cents": rolling or 0,
        "policy_candidate_rejection_reason": rejections,
        "business_outcome_coverage": outcomes[0] / max(1, n),
        "incorrect_outcome_rate": (bad or 0) / max(1, outcomes[0]),
        "realized_loss_cents": outcomes[1] or 0,
        "scope": "runtime only; synthetic history excluded",
    }


def text_interval_day() -> Any:
    from sqlalchemy import text

    return text("INTERVAL '1 day'")
