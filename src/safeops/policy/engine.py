from safeops.domain.models import Action, ToolRefused
from safeops.policy.models import (
    ApprovalRequirement,
    PolicyDecision,
    PolicyDefinition,
    RiskAssessment,
)
from safeops.risk.features import extract, risk_for
from safeops.risk.models import ActionRiskProfile, Decision, Features
from safeops.tools.registry import ToolRegistry


def authority(policy: PolicyDefinition, features: Features, risk: ActionRiskProfile) -> Decision:
    if features.action_type != "issue_refund":
        return "REVIEW" if features.action_type in policy.slo.mandatory_approval_tools else "AUTO"
    amount = features.exposure_cents
    if amount > policy.slo.hard_deny_amount_cents:
        return "DENY"
    coverage = risk.sample_count / max(1, risk.sample_count + risk.unknown_count)
    if (
        risk.sample_count < policy.slo.minimum_known_samples
        or coverage < policy.slo.minimum_outcome_coverage
    ):
        return "REVIEW"
    band = 0 if risk.risk_upper_bound <= 0.02 else 1 if risk.risk_upper_bound <= 0.08 else 2
    if amount > policy.review_limits[band]:
        return "DENY"
    return "AUTO" if amount <= policy.auto_limits[band] else "REVIEW"


class PolicyEngine:
    def __init__(self, policy: PolicyDefinition, registry: ToolRegistry):
        self.definition, self.registry = policy, registry

    def assess(self, action: Action) -> PolicyDecision:
        policy = self.definition
        try:
            spec = self.registry.require(action.tool, action.domain)
            features = extract(action)
            profile = risk_for(features, policy.calibration)
        except (ToolRefused, ValueError):
            return PolicyDecision(
                decision="DENY",
                version=policy.revision,
                policy_digest=policy.digest,
                risk=RiskAssessment(
                    score=100, level="critical", reasons=["invalid tool or arguments"]
                ),
            )
        verdict = authority(policy, features, profile)
        role = (
            "finance"
            if spec.risk_category == "financial"
            else "security"
            if spec.risk_category == "security"
            else "operator"
        )
        reasons = [
            "upper confidence bound × exposure",
            f"segment={features.segment}",
            f"samples={profile.sample_count}",
        ]
        if verdict == "REVIEW" and profile.sample_count < policy.slo.minimum_known_samples:
            reasons.append("cold or sparse segment: mandatory review")
        return PolicyDecision(
            decision={"AUTO": "ALLOW", "REVIEW": "REQUIRE_APPROVAL", "DENY": "DENY"}[verdict],
            version=policy.revision,
            policy_digest=policy.digest,
            requirement=ApprovalRequirement(role=role) if verdict == "REVIEW" else None,
            risk=RiskAssessment(
                score=round(profile.risk_upper_bound * 100)
                if spec.side_effect_type != "none"
                else 0,
                level="critical" if verdict == "DENY" else "high" if verdict == "REVIEW" else "low",
                reasons=reasons,
                profile=profile,
                features=features,
            ),
        )
