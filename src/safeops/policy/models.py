from typing import Literal

from pydantic import Field

from safeops.domain.models import Model, Role


class RiskAssessment(Model):
    score: int = Field(ge=0, le=100)
    level: Literal["low", "medium", "high", "critical"]
    reasons: list[str]


class ApprovalRequirement(Model):
    role: Role


class PolicyDecision(Model):
    decision: Literal["ALLOW", "REQUIRE_APPROVAL", "DENY", "ESCALATE"]
    risk: RiskAssessment
    requirement: ApprovalRequirement | None = None
    version: str
