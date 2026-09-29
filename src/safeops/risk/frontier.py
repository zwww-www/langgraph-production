from safeops.policy.models import CandidateResult


def frontier(candidates: list[CandidateResult]) -> list[CandidateResult]:
    def axes(c: CandidateResult) -> tuple[float, float, float]:
        p = c.replay.candidate
        return (p.upper_bound_loss_per_day_cents, p.review_load_per_hour, -p.auto_rate)

    result = []
    feasible = [c for c in candidates if c.feasible]
    for candidate in candidates:
        a = axes(candidate)
        dominated = any(
            all(x <= y for x, y in zip(axes(other), a, strict=True))
            and any(x < y for x, y in zip(axes(other), a, strict=True))
            for other in feasible
        )
        result.append(
            candidate.model_copy(update={"frontier": candidate.feasible and not dominated})
        )
    return result
