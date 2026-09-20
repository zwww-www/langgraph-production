import json
import re
from decimal import Decimal
from typing import Any

from safeops.llm.base import LLMRequest, LLMResponse


class OfflineProvider:
    """Deterministic bilingual demo classifier, not a claim about model quality."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = request.user.lower()
        invoice = re.search(r"INV-\d{5}", request.user)
        account = re.search(r"ACC-\d{4}", request.user)
        refund = any(x in text for x in ("refund", "退款"))
        cancel = any(x in text for x in ("cancel", "取消订阅"))
        reset = any(x in text for x in ("reset", "重置"))
        lock = any(x in text for x in ("lock account", "锁定"))
        subscription = any(x in text for x in ("subscription", "plan", "订阅", "套餐"))
        incident = any(x in text for x in ("breach", "leaked", "lawyer", "泄露", "律师"))
        hypothetical = any(x in text for x in ("how do", "usually", "what happens", "如果", "如何"))
        if request.tag == "supervisor":
            if incident or sum((refund, cancel, reset, lock)) > 1:
                route = "escalate"
            elif hypothetical:
                route = "answer"
            elif refund or invoice:
                route = "billing"
            elif subscription or cancel:
                route = "subscription"
            elif account or reset or lock:
                route = "account"
            else:
                route = "answer"
            output = {"route": route, "confidence": 0.9, "reason": "offline rule"}
        else:
            args: dict[str, Any] = {}
            tool = "unsupported"
            if request.tag == "billing" and invoice:
                args = {"invoice_ref": invoice.group()}
                tool = (
                    "get_payment_status"
                    if "payment" in text or "支付" in text
                    else "lookup_invoice"
                )
                if refund:
                    tool = "issue_refund"
                    amounts = re.findall(r"\$\s*([\d,]+(?:\.\d{1,2})?)", text)
                    if amounts:
                        cents = [int(Decimal(a.replace(",", "")) * 100) for a in amounts]
                        args["amount_cents"] = (
                            cents[0] - cents[1]
                            if len(cents) == 2 and "difference" in text
                            else cents[0]
                        )
            elif account:
                args = {"account_ref": account.group()}
                if request.tag == "account":
                    tool = (
                        "reset_credentials"
                        if reset
                        else "lock_account"
                        if lock
                        else "lookup_account"
                    )
                elif request.tag == "subscription":
                    tool = "cancel_subscription" if cancel else "get_subscription"
                    if any(x in text for x in ("upgrade", "downgrade", "change", "升级", "降级")):
                        tool = "change_plan"
                        plan = re.search(r"\b(free|pro|enterprise)\b", text)
                        if plan:
                            args["plan"] = plan.group()
            output = {"tool": tool, "args": args}
        return LLMResponse(text=json.dumps(output), model="offline-rules")
