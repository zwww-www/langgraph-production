from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from safeops.domain.models import Action, Model, Role

Decision = Literal["AUTO", "REVIEW", "DENY"]
Outcome = Literal["correct", "incorrect", "reversed", "disputed", "unknown"]


class Features(Model):
    action_type: str
    amount_bucket: int = Field(ge=0)
    exposure_cents: int = Field(ge=0)
    unavailable: tuple[str, ...] = (
        "customer_tenure",
        "prior_refund_count",
        "customer_tier",
        "prior_dispute",
    )

    @property
    def segment(self) -> str:
        return f"{self.action_type}:{self.amount_bucket}"


class RiskProfile(Model):
    segment: str
    sample_count: int = Field(ge=0)
    bad_outcome_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    observed_bad_rate: float = Field(ge=0, le=1)
    risk_upper_bound: float = Field(ge=0, le=1)
    confidence: float = 0.95
    realized_loss_cents: int = Field(ge=0)


class ActionRiskProfile(RiskProfile):
    exposure_cents: int = Field(ge=0)
    expected_loss_cents: int = Field(ge=0)


class Observation(Model):
    observation_id: str = Field(min_length=1, max_length=100)
    run_id: str
    action_id: str
    outcome: Outcome
    realized_loss_cents: int = Field(default=0, ge=0, le=2147483647, strict=True)
    observed_at: datetime
    source: Literal[
        "human_audit", "customer_dispute", "chargeback", "downstream_system", "reconciliation"
    ]
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_observation(self) -> "Observation":
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at requires timezone")
        if self.outcome in ("correct", "unknown") and self.realized_loss_cents:
            raise ValueError("correct/unknown cannot declare realized loss")
        return self


class History(Model):
    id: str
    action: Action
    features: Features
    decision: Decision
    arrived_at: datetime
    observed_at: datetime | None = None
    outcome: Outcome = "unknown"
    realized_loss_cents: int = 0
    review_latency_seconds: float | None = None


class ReviewCapacity(Model):
    role: Role
    reviewers: int = Field(ge=0)
    service_rate_per_hour: float = Field(gt=0)
    max_queue_depth: int = Field(ge=0)
    sla_seconds: int = Field(gt=0)
    queue_depth: int = Field(default=0, ge=0)

    @property
    def throughput(self) -> float:
        return self.reviewers * self.service_rate_per_hour


class BusinessSLO(Model):
    max_expected_loss_per_day_cents: int = Field(default=50000, ge=0)
    rolling_window_days: int = Field(default=7, ge=1, le=90)
    max_expected_loss_per_window_cents: int = Field(default=350000, ge=0)
    p95_resolution_seconds: int = Field(default=300, gt=0)
    target_auto_resolution_rate: float = Field(default=0.85, ge=0, le=1)
    hard_deny_amount_cents: int = Field(default=100000, gt=0)
    mandatory_approval_tools: tuple[str, ...] = (
        "reset_credentials",
        "lock_account",
        "change_plan",
        "cancel_subscription",
    )
    capacities: tuple[ReviewCapacity, ...] = (
        ReviewCapacity(
            role="finance",
            reviewers=2,
            service_rate_per_hour=10,
            max_queue_depth=20,
            sla_seconds=900,
        ),
        ReviewCapacity(
            role="security",
            reviewers=1,
            service_rate_per_hour=10,
            max_queue_depth=10,
            sla_seconds=900,
        ),
        ReviewCapacity(
            role="operator",
            reviewers=1,
            service_rate_per_hour=20,
            max_queue_depth=20,
            sla_seconds=600,
        ),
    )
    review_cost_cents: int = Field(default=150, ge=0)
    delay_cost_cents_per_second: float = Field(default=0.1, ge=0)
    reviewer_error_probability: float = Field(default=0, ge=0, le=1)
    denial_cost_cents: int = Field(default=600, ge=0)
    auto_resolution_seconds: int = Field(default=5, gt=0)
    fallback_arrivals_per_hour: float = Field(default=100, gt=0)
    fallback_review_seconds: int = Field(default=900, gt=0)
    minimum_known_samples: int = Field(default=30, ge=1)
    minimum_outcome_coverage: float = Field(default=0.7, ge=0, le=1)

    @model_validator(mode="after")
    def unique_roles(self) -> "BusinessSLO":
        roles = [c.role for c in self.capacities]
        if len(set(roles)) != len(roles) or "finance" not in roles:
            raise ValueError("unique reviewer roles including finance required")
        if not {"reset_credentials", "lock_account", "change_plan", "cancel_subscription"}.issubset(
            self.mandatory_approval_tools
        ):
            raise ValueError("sensitive account/subscription tools must retain mandatory approval")
        return self


class Calibration(Model):
    method: Literal["wilson-one-sided-95"] = "wilson-one-sided-95"
    profiles: dict[str, RiskProfile] = Field(default_factory=dict)
    history_count: int = 0
    known_count: int = 0
    data_digest: str
    window_start: datetime
    window_end: datetime
    split_at: datetime
    source: Literal["runtime", "synthetic", "bootstrap"]


class Projection(Model):
    sample_count: int
    auto_rate: float
    review_rate: float
    deny_rate: float
    expected_loss_per_day_cents: float
    upper_bound_loss_per_day_cents: float
    upper_bound_loss_per_window_cents: float
    review_load_per_hour: float
    review_capacity_utilization: float
    review_p95_seconds: float
    resolution_p95_seconds: float
    total_business_cost_cents: float
    outcome_coverage: float
    realized_loss_cents: int
    data_quality: str
    errors: list[str] = Field(default_factory=list)

    @property
    def feasible(self) -> bool:
        return not self.errors
