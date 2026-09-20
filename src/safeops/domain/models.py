import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Domain = Literal["billing", "account", "subscription"]
Role = Literal["operator", "finance", "security", "admin"]


def now() -> datetime:
    return datetime.now(UTC)


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Ticket(Model):
    id: str = Field(min_length=1, max_length=100)
    customer_id: str = Field(pattern=r"^CUS-[A-Za-z0-9-]+$")
    text: str = Field(min_length=1, max_length=8000)


class SupervisorDecision(Model):
    route: Literal["billing", "account", "subscription", "answer", "escalate"]
    confidence: float = Field(ge=0, le=1, strict=True)
    reason: str = Field(default="", max_length=500)


class ToolCall(Model):
    tool: str
    args: dict[str, Any]


class Action(Model):
    action_id: str
    tool: str
    args: dict[str, Any]
    domain: Domain

    @classmethod
    def build(cls, ticket: Ticket, tool: str, args: dict[str, Any], domain: Domain) -> "Action":
        return cls(
            action_id=digest(
                {
                    "customer": ticket.customer_id,
                    "request": ticket.id,
                    "tool": tool,
                    "args": args,
                    "domain": domain,
                }
            ),
            tool=tool,
            args=args,
            domain=domain,
        )


class Principal(Model):
    name: str = Field(min_length=1)
    role: Role


class Receipt(Model):
    action_id: str
    idempotency_key: str
    tool: str
    result: dict[str, Any]
    performed_at: datetime
    replayed: bool = False
    dry_run: bool = False


class SafeOpsError(Exception):
    """Safe, non-sensitive error suitable for an API response."""


class Conflict(SafeOpsError):
    pass


class Forbidden(SafeOpsError):
    pass


class NotFound(SafeOpsError):
    pass


class EffectInDoubt(SafeOpsError):
    pass


class ToolRefused(SafeOpsError):
    """Adapter certifies that no operation occurred."""
