from collections import Counter

from safeops.domain.models import digest
from safeops.policy.engine import authority
from safeops.policy.models import PolicyDefinition, ReplayResult
from safeops.risk.budget import review_projection
from safeops.risk.features import risk_for
from safeops.risk.models import History, Projection


def decisions(policy: PolicyDefinition, rows: list[History]) -> list[str]:
    # Group by immutable segment/exposure; no LLM and no per-action SQL.
    cache: dict[tuple[str, int], str] = {}
    result = []
    for row in rows:
        key = (row.features.segment, row.features.exposure_cents)
        if key not in cache:
            cache[key] = authority(policy, row.features, risk_for(row.features, policy.calibration))
        result.append(cache[key])
    return result


def project(policy: PolicyDefinition, rows: list[History], verdicts: list[str]) -> Projection:
    slo = policy.slo
    count = len(rows)
    counts = Counter(verdicts)
    auto, review, deny = (counts[x] / max(1, count) for x in ("AUTO", "REVIEW", "DENY"))
    arrivals, utilization, latency, quality = review_projection(rows, review, slo)
    observed_loss = upper_loss = cost = 0.0
    features = {r.features.segment: r.features for r in rows}
    risks = {key: risk_for(value, policy.calibration) for key, value in features.items()}
    for row, verdict in zip(rows, verdicts, strict=True):
        profile = risks[row.features.segment]
        exposure = row.features.exposure_cents
        if verdict == "AUTO":
            observed_loss += profile.observed_bad_rate * exposure
            upper_loss += profile.risk_upper_bound * exposure
            cost += profile.risk_upper_bound * exposure
        elif verdict == "REVIEW":
            observed_loss += slo.reviewer_error_probability * exposure
            upper_loss += slo.reviewer_error_probability * exposure
            cost += (
                slo.review_cost_cents
                + latency * slo.delay_cost_cents_per_second
                + slo.reviewer_error_probability * exposure
            )
        else:
            cost += slo.denial_cost_cents
    scale = arrivals * 24 / max(1, count)
    daily = upper_loss * scale
    resolution = latency if review > 0.05 else slo.auto_resolution_seconds
    capacity = next(c for c in slo.capacities if c.role == "finance")
    errors = []
    if not count:
        errors.append("empty replay history")
    if daily > slo.max_expected_loss_per_day_cents:
        errors.append("daily risk budget exceeded")
    if daily * slo.rolling_window_days > slo.max_expected_loss_per_window_cents:
        errors.append("rolling risk budget exceeded")
    if utilization > 1:
        errors.append("review capacity exceeded")
    if capacity.queue_depth + arrivals * review > capacity.max_queue_depth:
        errors.append("review queue depth exceeded")
    if latency > capacity.sla_seconds:
        errors.append("review queue SLA exceeded")
    if resolution > slo.p95_resolution_seconds:
        errors.append("resolution SLA exceeded")
    if auto < slo.target_auto_resolution_rate:
        errors.append("automation target not met")
    return Projection(
        sample_count=count,
        auto_rate=auto,
        review_rate=review,
        deny_rate=deny,
        expected_loss_per_day_cents=observed_loss * scale,
        upper_bound_loss_per_day_cents=daily,
        upper_bound_loss_per_window_cents=daily * slo.rolling_window_days,
        review_load_per_hour=arrivals * review,
        review_capacity_utilization=utilization,
        review_p95_seconds=latency,
        resolution_p95_seconds=resolution,
        total_business_cost_cents=cost,
        outcome_coverage=sum(r.outcome != "unknown" for r in rows) / max(1, count),
        realized_loss_cents=sum(r.realized_loss_cents for r in rows),
        data_quality=quality,
        errors=errors,
    )


def transitions(old: list[str], new: list[str]) -> dict[str, int]:
    counts = {
        f"{a}->{b}": 0
        for a in ("AUTO", "REVIEW", "DENY")
        for b in ("AUTO", "REVIEW", "DENY")
        if a != b
    }
    counts["unchanged"] = 0
    for a, b in zip(old, new, strict=True):
        counts["unchanged" if a == b else f"{a}->{b}"] += 1
    return counts


def replay(
    current: PolicyDefinition, candidate: PolicyDefinition, rows: list[History]
) -> ReplayResult:
    before, after = decisions(current, rows), decisions(candidate, rows)
    changes = transitions(before, after)
    return ReplayResult(
        current=project(current, rows, before),
        candidate=project(candidate, rows, after),
        transitions=changes,
        historical_transitions=transitions([r.decision for r in rows], after),
        changed_count=len(rows) - changes["unchanged"],
        deny_to_auto=changes["DENY->AUTO"],
        sample_digest=digest([r.id for r in rows]),
    )
