from typing import Literal

from pydantic import Field

from safeops.domain.models import Model, Role
from safeops.domain.models import digest as content_digest
from safeops.risk.models import ActionRiskProfile, BusinessSLO, Calibration, Features, Projection


class RiskAssessment(Model):
    score: int = Field(ge=0, le=100)
    level: Literal["low", "medium", "high", "critical"]
    reasons: list[str]
    profile: ActionRiskProfile | None = None
    features: Features | None = None


class ApprovalRequirement(Model):
    role: Role


class PolicyDecision(Model):
    decision: Literal["ALLOW", "REQUIRE_APPROVAL", "DENY", "ESCALATE"]
    risk: RiskAssessment
    requirement: ApprovalRequirement | None = None
    version: str
    policy_digest: str = ""


class PolicyDefinition(Model):
    revision: str
    slo: BusinessSLO
    calibration: Calibration
    auto_limits: tuple[int, int, int]
    review_limits: tuple[int, int, int]

    @property
    def digest(self) -> str:
        return content_digest(self.model_dump(mode="json"))


class ReplayResult(Model):
    current: Projection
    candidate: Projection
    transitions: dict[str, int]
    historical_transitions: dict[str, int]
    changed_count: int
    deny_to_auto: int
    sample_digest: str


class CandidateResult(Model):
    id: str
    policy: PolicyDefinition
    replay: ReplayResult
    feasible: bool
    errors: list[str]
    frontier: bool = False
