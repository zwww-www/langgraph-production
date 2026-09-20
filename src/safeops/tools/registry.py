from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import Field, field_validator

from safeops.domain.models import Domain, Model, ToolRefused


class InvoiceArgs(Model):
    invoice_ref: str = Field(pattern=r"^INV-\d{5}$")


class RefundArgs(InvoiceArgs):
    amount_cents: int = Field(gt=0, le=1000000)

    @field_validator("amount_cents", mode="before")
    @classmethod
    def no_bool(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("boolean is not an amount")
        return value


class AccountArgs(Model):
    account_ref: str = Field(pattern=r"^ACC-\d{4}$")


class PlanArgs(AccountArgs):
    plan: Literal["free", "pro", "enterprise"]


Handler = Callable[[dict[str, Any], str, str], Awaitable[dict[str, Any]]]


async def unbound(args: dict[str, Any], key: str, customer: str) -> dict[str, Any]:
    raise ToolRefused("tool adapter not configured")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_schema: type[Model]
    domain: Domain
    risk_category: Literal["read", "financial", "security", "subscription"]
    side_effect_type: Literal["none", "reversible", "irreversible"]
    requires_idempotency: bool
    handler: Handler = unbound

    def validate(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.args_schema.model_validate(args).model_dump(mode="json")


SPECS = (
    ToolSpec("lookup_invoice", "Read owned invoice", InvoiceArgs, "billing", "read", "none", False),
    ToolSpec(
        "get_payment_status", "Investigate payment", InvoiceArgs, "billing", "read", "none", False
    ),
    ToolSpec(
        "issue_refund",
        "Refund paid invoice",
        RefundArgs,
        "billing",
        "financial",
        "irreversible",
        True,
    ),
    ToolSpec("lookup_account", "Read owned account", AccountArgs, "account", "read", "none", False),
    ToolSpec(
        "reset_credentials",
        "Invalidate sessions and send reset",
        AccountArgs,
        "account",
        "security",
        "irreversible",
        True,
    ),
    ToolSpec(
        "lock_account", "Lock owned account", AccountArgs, "account", "security", "reversible", True
    ),
    ToolSpec(
        "get_subscription", "Read subscription", AccountArgs, "subscription", "read", "none", False
    ),
    ToolSpec(
        "change_plan",
        "Change subscription plan",
        PlanArgs,
        "subscription",
        "subscription",
        "reversible",
        True,
    ),
    ToolSpec(
        "cancel_subscription",
        "Cancel subscription",
        AccountArgs,
        "subscription",
        "subscription",
        "irreversible",
        True,
    ),
)


class ToolRegistry:
    def __init__(self, specs: tuple[ToolSpec, ...] = SPECS):
        self.specs = {spec.name: spec for spec in specs}
        if len(self.specs) != len(specs):
            raise ValueError("duplicate tool name")

    def require(self, name: str, domain: str | None = None) -> ToolSpec:
        spec = self.specs.get(name)
        if not spec or (domain is not None and spec.domain != domain):
            raise ToolRefused("tool not allowed in this domain")
        return spec

    def bind(self, name: str, handler: Handler) -> None:
        self.specs[name] = replace(self.require(name), handler=handler)

    def catalogue(self, domain: str) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "schema": s.args_schema.model_json_schema(),
            }
            for s in self.specs.values()
            if s.domain == domain
        ]
