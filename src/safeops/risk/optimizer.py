from itertools import combinations_with_replacement
from typing import Any

from safeops.domain.models import digest
from safeops.policy.compiler import validate
from safeops.policy.models import CandidateResult, PolicyDefinition
from safeops.policy.simulator import replay
from safeops.risk.frontier import frontier
from safeops.risk.models import BusinessSLO, Calibration, History

AUTO_FRACTIONS = (0, 0.01, 0.02, 0.05, 0.1, 0.3, 1)
OPTIMIZER_CONFIG: dict[str, Any] = {
    "algorithm": "constrained-grid",
    "auto_fractions": list(AUTO_FRACTIONS),
    "review_fractions": [0.3, 1],
    "temporal_training_fraction": 0.6,
}


def optimize(
    current: PolicyDefinition,
    slo: BusinessSLO,
    calibration: Calibration,
    rows: list[History],
    epoch: int = 0,
) -> list[CandidateResult]:
    values = sorted(set(int(slo.hard_deny_amount_cents * x) for x in AUTO_FRACTIONS))
    results = []
    for limits in combinations_with_replacement(values, 3):
        auto = tuple(reversed(limits))
        for fraction in (0.3, 1.0):
            review = (
                slo.hard_deny_amount_cents,
                slo.hard_deny_amount_cents,
                int(slo.hard_deny_amount_cents * fraction),
            )
            if auto[2] > review[2]:
                continue
            identity = digest(
                {
                    "slo": slo.model_dump(mode="json"),
                    "calibration": calibration.model_dump(mode="json"),
                    "auto": auto,
                    "review": review,
                    "optimizer": OPTIMIZER_CONFIG,
                    "base_revision": current.revision,
                    "data_epoch": epoch,
                }
            )
            policy = PolicyDefinition(
                revision=identity,
                slo=slo,
                calibration=calibration,
                auto_limits=auto,
                review_limits=review,
            )
            diff = replay(current, policy, rows)
            errors = validate(policy, slo) + diff.candidate.errors
            results.append(
                CandidateResult(
                    id=identity, policy=policy, replay=diff, feasible=not errors, errors=errors
                )
            )
    return frontier(results)


def recommended(candidates: list[CandidateResult]) -> str | None:
    feasible = [c for c in candidates if c.feasible]
    return (
        min(feasible, key=lambda c: (c.replay.candidate.total_business_cost_cents, c.id)).id
        if feasible
        else None
    )
