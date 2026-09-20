from dataclasses import dataclass, field

CRASH_POINTS = (
    "before_plan",
    "after_plan",
    "before_approval",
    "after_approval",
    "effect_claimed",
    "external_effect_executed",
    "effect_recorded",
    "before_compose",
    "after_compose",
)


class SimulatedCrash(BaseException):
    """Bypasses business exception handling like a process termination."""


@dataclass
class Faults:
    point: str | None = None
    fired: bool = False
    seen: list[str] = field(default_factory=list)

    def hit(self, point: str) -> None:
        self.seen.append(point)
        if point == self.point and not self.fired:
            self.fired = True
            raise SimulatedCrash(point)
