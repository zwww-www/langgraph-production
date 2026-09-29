from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from safeops.domain.models import now


class Base(DeclarativeBase):
    pass


class TicketRow(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(100), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RunRow(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    domain: Mapped[str | None] = mapped_column(String(32))
    risk: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dry_run: Mapped[bool] = mapped_column(default=False)
    initial_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("one_live_run_per_ticket", "ticket_id", unique=True, postgresql_where=~dry_run),
    )


class EventRow(Base):
    __tablename__ = "agent_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    thread_id: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(60))
    node: Mapped[str] = mapped_column(String(60))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ApprovalRow(Base):
    __tablename__ = "approvals"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    thread_id: Mapped[str] = mapped_column(String(64))
    action_id: Mapped[str | None] = mapped_column(String(64))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    approver: Mapped[str | None] = mapped_column(String(100))
    approver_role: Mapped[str | None] = mapped_column(String(32))
    decision: Mapped[str | None] = mapped_column(String(16), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EffectRow(Base):
    __tablename__ = "effects"
    idempotency_key: Mapped[str] = mapped_column(String(140), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    tool: Mapped[str] = mapped_column(String(100))
    args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    execution_count: Mapped[int] = mapped_column(Integer, default=0)
    receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ExecutionRow(Base):
    __tablename__ = "effect_executions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    effect_id: Mapped[str] = mapped_column(ForeignKey("effects.idempotency_key"))
    attempt: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("effect_id", "attempt"),)


class ReconciliationRow(Base):
    __tablename__ = "reconciliations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    effect_id: Mapped[str] = mapped_column(ForeignKey("effects.idempotency_key"))
    outcome: Mapped[str] = mapped_column(String(32))
    resolver: Mapped[str] = mapped_column(String(100))
    evidence: Mapped[str] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemoryRow(Base):
    __tablename__ = "customer_memories"
    customer_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvaluationRow(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ExternalRecordRow(Base):
    """Synthetic downstream business state; commits independently of the effect ledger."""

    __tablename__ = "demo_external_records"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(100))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ExternalOperationRow(Base):
    __tablename__ = "demo_external_operations"
    key: Mapped[str] = mapped_column(String(140), primary_key=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SLORow(Base):
    __tablename__ = "business_slos"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    author: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CalibrationRow(Base):
    __tablename__ = "risk_calibrations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(32), index=True)
    data_epoch: Mapped[int] = mapped_column(BigInteger)
    sample_count: Mapped[int] = mapped_column(Integer)
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("history_datasets.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CandidateRow(Base):
    __tablename__ = "policy_candidates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slo_id: Mapped[str] = mapped_column(ForeignKey("business_slos.id"))
    calibration_id: Mapped[str] = mapped_column(ForeignKey("risk_calibrations.id"))
    base_revision: Mapped[str] = mapped_column(
        ForeignKey("policy_revisions.id", name="candidate_base_revision", use_alter=True)
    )
    source: Mapped[str] = mapped_column(String(32))
    data_epoch: Mapped[int] = mapped_column(BigInteger)
    feasible: Mapped[bool]
    frontier: Mapped[bool]
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result_digest: Mapped[str] = mapped_column(String(64))
    optimizer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PolicyRevisionRow(Base):
    __tablename__ = "policy_revisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slo_id: Mapped[str] = mapped_column(ForeignKey("business_slos.id"))
    calibration_id: Mapped[str] = mapped_column(ForeignKey("risk_calibrations.id"))
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("policy_candidates.id"))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReleaseRow(Base):
    __tablename__ = "policy_releases"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("policy_revisions.id"), unique=True)
    principal: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    diff_digest: Mapped[str] = mapped_column(String(64))
    slo_digest: Mapped[str] = mapped_column(String(64))
    calibration_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ControlStateRow(Base):
    __tablename__ = "control_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slo_id: Mapped[str] = mapped_column(ForeignKey("business_slos.id"))
    revision_id: Mapped[str] = mapped_column(ForeignKey("policy_revisions.id"))
    data_epoch: Mapped[int] = mapped_column(BigInteger, default=0)


class DatasetRow(Base):
    __tablename__ = "history_datasets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seed: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ActionHistoryRow(Base):
    __tablename__ = "action_history"
    id: Mapped[str] = mapped_column(String(140), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    action_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("history_datasets.id"), index=True)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("policy_revisions.id"))
    customer_id: Mapped[str] = mapped_column(String(100))
    tool: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[dict[str, Any]] = mapped_column(JSONB)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB)
    decision: Mapped[str] = mapped_column(String(16))
    expected_loss_cents: Mapped[int] = mapped_column(Integer)
    review_latency_seconds: Mapped[float | None]
    receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    postcondition: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    arrived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class OutcomeRow(Base):
    __tablename__ = "business_outcomes"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    history_id: Mapped[str] = mapped_column(ForeignKey("action_history.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    realized_loss_cents: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(40))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64))
    observer: Mapped[str] = mapped_column(String(100))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        Index(
            "one_final_outcome", "history_id", unique=True, postgresql_where=(outcome != "unknown")
        ),
    )
