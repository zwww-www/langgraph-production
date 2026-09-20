from dataclasses import dataclass

from safeops.approvals.service import Approvals
from safeops.config import Settings
from safeops.effects.service import Effects
from safeops.events.service import Events
from safeops.faults import Faults
from safeops.llm.base import Provider
from safeops.memory.service import Memory
from safeops.policy.engine import PolicyEngine
from safeops.tools.registry import ToolRegistry


@dataclass
class Dependencies:
    settings: Settings
    provider: Provider
    registry: ToolRegistry
    policy: PolicyEngine
    approvals: Approvals
    effects: Effects
    events: Events
    memory: Memory
    faults: Faults
