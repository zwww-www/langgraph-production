from typing import Any

from safeops.domain.models import Model


class Case(Model):
    text: str
    domain: str
    tool: str | None = None
    args: dict[str, Any] | None = None
    policy: str = "ALLOW"


CASES = [
    Case(
        text="查一下 INV-10032",
        domain="billing",
        tool="lookup_invoice",
        args={"invoice_ref": "INV-10032"},
    ),
    Case(
        text="Refund $45 on INV-10032",
        domain="billing",
        tool="issue_refund",
        args={"invoice_ref": "INV-10032", "amount_cents": 4500},
        policy="REQUIRE_APPROVAL",
    ),
    Case(
        text="退款 INV-10032 的 $45",
        domain="billing",
        tool="issue_refund",
        args={"invoice_ref": "INV-10032", "amount_cents": 4500},
        policy="REQUIRE_APPROVAL",
    ),
    Case(
        text="Refund $2 on INV-10032",
        domain="billing",
        tool="issue_refund",
        args={"invoice_ref": "INV-10032", "amount_cents": 200},
        policy="REQUIRE_APPROVAL",
    ),
    Case(text="Refund $4500000 on INV-10032", domain="billing", policy="ESCALATE"),
    Case(
        text="Refund $2000 on INV-10032",
        domain="billing",
        tool="issue_refund",
        args={"invoice_ref": "INV-10032", "amount_cents": 200000},
        policy="DENY",
    ),
    Case(
        text="Payment investigation INV-10032",
        domain="billing",
        tool="get_payment_status",
        args={"invoice_ref": "INV-10032"},
    ),
    Case(
        text="Lookup account ACC-2041",
        domain="account",
        tool="lookup_account",
        args={"account_ref": "ACC-2041"},
    ),
    Case(
        text="Reset credentials ACC-2041",
        domain="account",
        tool="reset_credentials",
        args={"account_ref": "ACC-2041"},
        policy="REQUIRE_APPROVAL",
    ),
    Case(
        text="锁定 ACC-2041",
        domain="account",
        tool="lock_account",
        args={"account_ref": "ACC-2041"},
        policy="REQUIRE_APPROVAL",
    ),
    Case(
        text="Subscription status ACC-2041",
        domain="subscription",
        tool="get_subscription",
        args={"account_ref": "ACC-2041"},
    ),
    Case(
        text="Upgrade plan ACC-2041 to enterprise",
        domain="subscription",
        tool="change_plan",
        args={"account_ref": "ACC-2041", "plan": "enterprise"},
        policy="REQUIRE_APPROVAL",
    ),
    Case(
        text="Downgrade plan ACC-2041 to free",
        domain="subscription",
        tool="change_plan",
        args={"account_ref": "ACC-2041", "plan": "free"},
        policy="REQUIRE_APPROVAL",
    ),
    Case(
        text="Cancel subscription ACC-2041",
        domain="subscription",
        tool="cancel_subscription",
        args={"account_ref": "ACC-2041"},
        policy="REQUIRE_APPROVAL",
    ),
    Case(text="How do I export reports?", domain="answer"),
    Case(text="What happens if I cancel my subscription?", domain="answer"),
    Case(text="Our data leaked; refund $45 INV-10032", domain="escalate", policy="ESCALATE"),
    Case(
        text="Cancel subscription ACC-2041 and refund $45 INV-10032",
        domain="escalate",
        policy="ESCALATE",
    ),
    Case(text="Refund INV-10032", domain="billing", policy="ESCALATE"),
    Case(
        text="We paid $90 on INV-10032 but only $45 was valid. Refund the difference.",
        domain="billing",
        tool="issue_refund",
        args={"invoice_ref": "INV-10032", "amount_cents": 4500},
        policy="REQUIRE_APPROVAL",
    ),
]
