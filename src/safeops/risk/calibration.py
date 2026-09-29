from collections import defaultdict
from math import sqrt

from safeops.domain.models import digest
from safeops.risk.models import Calibration, History, RiskProfile


def wilson_upper(bad: int, n: int) -> float:
    if n < 0 or bad < 0 or bad > n:
        raise ValueError("invalid binomial counts")
    if n == 0:
        return 1.0
    z = 1.6448536269514722  # One-sided 95% normal quantile.
    p = bad / n
    return min(
        1.0,
        (p + z * z / (2 * n) + z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n),
    )


def calibrate(rows: list[History], source: str) -> tuple[Calibration, list[History]]:
    if len(rows) < 10:
        raise ValueError("at least 10 historical actions required")
    ordered = sorted(rows, key=lambda r: (r.arrived_at, r.id))
    split = max(1, int(len(ordered) * 0.6))
    training, validation = ordered[:split], ordered[split:]
    # A temporal backtest cannot use outcomes that arrived after training ended.
    training = [
        r.model_copy(update={"outcome": "unknown", "realized_loss_cents": 0})
        if r.observed_at is None or r.observed_at >= validation[0].arrived_at
        else r
        for r in training
    ]
    groups: dict[str, list[History]] = defaultdict(list)
    for row in training:
        groups[row.features.segment].append(row)
    profiles = {}
    for segment, group in groups.items():
        known = [r for r in group if r.outcome != "unknown"]
        bad = sum(r.outcome != "correct" for r in known)
        profiles[segment] = RiskProfile(
            segment=segment,
            sample_count=len(known),
            bad_outcome_count=bad,
            unknown_count=len(group) - len(known),
            observed_bad_rate=bad / len(known) if known else 0,
            risk_upper_bound=wilson_upper(bad, len(known)),
            realized_loss_cents=sum(r.realized_loss_cents for r in known),
        )
    snapshot = Calibration(
        profiles=profiles,
        history_count=len(training),
        known_count=sum(r.outcome != "unknown" for r in training),
        data_digest=digest([r.model_dump(mode="json") for r in ordered]),
        window_start=ordered[0].arrived_at,
        window_end=ordered[-1].arrived_at,
        split_at=validation[0].arrived_at,
        source=source,
    )
    return snapshot, validation
