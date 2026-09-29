from math import ceil

from safeops.risk.models import BusinessSLO, History


def percentile95(values: list[float]) -> float:
    return sorted(values)[max(0, ceil(len(values) * 0.95) - 1)] if values else 0


def review_projection(
    rows: list[History], review_rate: float, slo: BusinessSLO
) -> tuple[float, float, float, str]:
    hours = (
        (max(r.arrived_at for r in rows) - min(r.arrived_at for r in rows)).total_seconds() / 3600
        if rows
        else 0
    )
    arrivals = (
        len(rows) / hours
        if hours >= 24
        else max(slo.fallback_arrivals_per_hour, len(rows) / max(1, hours))
    )
    samples = [r.review_latency_seconds for r in rows if r.review_latency_seconds is not None]
    latency = percentile95(samples) if len(samples) >= 30 else float(slo.fallback_review_seconds)
    capacity = next(c for c in slo.capacities if c.role == "finance")
    demand = arrivals * review_rate
    utilization = demand / capacity.throughput if capacity.throughput else (1e9 if demand else 0)
    # Explicit deterministic workload bound, not a queueing-distribution claim.
    backlog_delay = 3600 * (capacity.queue_depth + demand) / max(capacity.throughput, 0.001)
    projected = max(latency, backlog_delay) if demand else 0
    quality = (
        "historical p95 + one-hour workload bound"
        if len(samples) >= 30 and hours >= 24
        else "conservative fallback: sparse latency/arrival history"
    )
    return arrivals, utilization, projected, quality
