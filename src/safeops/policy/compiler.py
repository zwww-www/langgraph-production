from safeops.policy.engine import authority
from safeops.policy.models import PolicyDefinition
from safeops.risk.features import AMOUNT_BOUNDARIES, risk_for
from safeops.risk.models import BusinessSLO, Features


def validate(policy: PolicyDefinition, safety: BusinessSLO) -> list[str]:
    errors = []
    if policy.slo.hard_deny_amount_cents > safety.hard_deny_amount_cents:
        errors.append("hard deny boundary relaxed")
    if not set(safety.mandatory_approval_tools).issubset(policy.slo.mandatory_approval_tools):
        errors.append("mandatory approval constraint relaxed")
    for limits in (policy.auto_limits, policy.review_limits):
        if not limits[0] >= limits[1] >= limits[2] >= 0:
            errors.append("risk monotonicity violation")
    if any(
        a > r or r > safety.hard_deny_amount_cents
        for a, r in zip(policy.auto_limits, policy.review_limits, strict=True)
    ):
        errors.append("hard constraint violation")
    # Calibration can vary by amount bucket; check the composed policy, not only its thresholds.
    points = {1, safety.hard_deny_amount_cents + 1}
    for limit in (*AMOUNT_BOUNDARIES, *policy.auto_limits, *policy.review_limits):
        points.update((max(1, limit), limit + 1))
    rank = {"AUTO": 0, "REVIEW": 1, "DENY": 2}
    last = 0
    for amount in sorted(points):
        f = Features(
            action_type="issue_refund",
            amount_bucket=sum(amount > b for b in AMOUNT_BOUNDARIES),
            exposure_cents=amount,
        )
        decision = rank[authority(policy, f, risk_for(f, policy.calibration))]
        if decision < last:
            errors.append("amount monotonicity violation")
            break
        last = decision
    return sorted(set(errors))
