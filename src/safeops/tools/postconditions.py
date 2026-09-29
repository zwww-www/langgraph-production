from typing import Any

from sqlalchemy import select

from safeops.config import Settings
from safeops.domain.models import Action, Forbidden, Receipt
from safeops.persistence.database import Database
from safeops.persistence.models import (
    ActionHistoryRow,
    ApprovalRow,
    ControlStateRow,
    ExternalOperationRow,
    ExternalRecordRow,
)
from safeops.policy.registry import control_lock


async def verify_postcondition(
    db: Database, settings: Settings, run_id: str, action: Action, receipt: Receipt
) -> dict[str, Any]:
    result: dict[str, Any] = {"verification_status": "unavailable"}
    if receipt.dry_run:
        return result | {"reason": "dry-run has no business postcondition"}
    async with db.sessions.begin() as session:
        await control_lock(session)
        if action.tool not in settings.tool_bindings:
            operation = await session.get(ExternalOperationRow, receipt.idempotency_key)
            ref = action.args.get("invoice_ref") or action.args.get("account_ref")
            record = await session.get(ExternalRecordRow, ref)
            if operation:
                matched = operation.result == receipt.result
                data = record.data if record else {}
                if action.tool == "issue_refund":
                    matched &= (
                        operation.result.get("refunded_cents") == action.args["amount_cents"]
                        and data.get("refunded_cents", -1) >= action.args["amount_cents"]
                    )
                if action.tool == "reset_credentials":
                    matched &= data.get("reset_count", 0) > 0
                if action.tool == "lock_account":
                    matched &= data.get("locked") is True
                if action.tool == "change_plan":
                    matched &= data.get("plan") == action.args["plan"]
                if action.tool == "cancel_subscription":
                    matched &= data.get("status") == "cancelled"
                result = {
                    "verification_status": "verified" if matched else "failed",
                    "evidence": "independent local operation + current business state",
                }
            elif action.tool in (
                "lookup_invoice",
                "get_payment_status",
                "lookup_account",
                "get_subscription",
            ):
                result = {
                    "verification_status": "unavailable",
                    "reason": "read-only snapshot; no mutation to verify",
                }
            else:
                result = {"verification_status": "failed", "reason": "local operation missing"}
        else:
            result["reason"] = "adapter does not implement independent verification"
        history = await session.get(ActionHistoryRow, f"{run_id}:{action.action_id}")
        if history:
            state = await session.get(ControlStateRow, 1)
            if state:
                state.data_epoch += 1
            history.receipt = receipt.model_dump(mode="json")
            history.postcondition = result
            approval = await session.scalar(
                select(ApprovalRow).where(
                    ApprovalRow.run_id == run_id,
                    ApprovalRow.action_id == action.action_id,
                    ApprovalRow.decided_at.is_not(None),
                )
            )
            if approval and approval.decided_at:
                history.review_latency_seconds = (
                    approval.decided_at - approval.created_at
                ).total_seconds()
    if result["verification_status"] == "failed":
        raise Forbidden("immediate postcondition verification failed")
    return result
