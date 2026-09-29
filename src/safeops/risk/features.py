from math import ceil

from safeops.domain.models import Action
from safeops.risk.models import ActionRiskProfile, Calibration, Features, RiskProfile
from safeops.tools.registry import ToolRegistry

# Half-open upper boundaries are shared by calibration, replay and runtime.
AMOUNT_BOUNDARIES = (1000, 2000, 5000, 10000, 30000, 100000)


def extract(action: Action) -> Features:
    args = ToolRegistry().require(action.tool, action.domain).validate(action.args)
    exposure = args["amount_cents"] if action.tool == "issue_refund" else 0
    bucket = sum(exposure > boundary for boundary in AMOUNT_BOUNDARIES)
    return Features(action_type=action.tool, amount_bucket=bucket, exposure_cents=exposure)


def risk_for(features: Features, calibration: Calibration) -> ActionRiskProfile:
    profile = calibration.profiles.get(features.segment) or RiskProfile(
        segment=features.segment,
        sample_count=0,
        bad_outcome_count=0,
        unknown_count=0,
        observed_bad_rate=0,
        risk_upper_bound=1,
        realized_loss_cents=0,
    )
    return ActionRiskProfile(
        **profile.model_dump(),
        exposure_cents=features.exposure_cents,
        expected_loss_cents=ceil(profile.risk_upper_bound * features.exposure_cents),
    )
