from safeops.config import Settings
from safeops.domain.models import Action, Role, ToolRefused
from safeops.policy.models import ApprovalRequirement, PolicyDecision, RiskAssessment
from safeops.tools.registry import ToolRegistry


class PolicyEngine:
    def __init__(self, settings: Settings, registry: ToolRegistry):
        self.settings, self.registry = settings, registry

    def assess(self, action: Action) -> PolicyDecision:
        version = self.settings.policy_version
        try:
            spec = self.registry.require(action.tool, action.domain)
            args = spec.validate(action.args)
        except (ToolRefused, ValueError):
            return PolicyDecision(
                decision="DENY",
                version=version,
                risk=RiskAssessment(
                    score=100, level="critical", reasons=["invalid tool or arguments"]
                ),
            )
        if spec.side_effect_type == "none":
            return PolicyDecision(
                decision="ALLOW",
                version=version,
                risk=RiskAssessment(score=5, level="low", reasons=["read-only scoped lookup"]),
            )
        role: Role
        if spec.risk_category == "financial":
            amount = args["amount_cents"]
            if amount > self.settings.refund_deny_cents:
                return PolicyDecision(
                    decision="DENY",
                    version=version,
                    risk=RiskAssessment(
                        score=100, level="critical", reasons=["refund exceeds limit"]
                    ),
                )
            if amount <= self.settings.refund_approval_cents:
                return PolicyDecision(
                    decision="ALLOW",
                    version=version,
                    risk=RiskAssessment(
                        score=30, level="medium", reasons=["within automatic refund limit"]
                    ),
                )
            role = "finance"
        elif spec.risk_category == "security":
            role = "security"
        else:
            role = "operator"
        return PolicyDecision(
            decision="REQUIRE_APPROVAL",
            version=version,
            requirement=ApprovalRequirement(role=role),
            risk=RiskAssessment(
                score=80, level="high", reasons=[f"{spec.risk_category} change requires review"]
            ),
        )
