from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from safeops.domain.models import ToolRefused, digest
from safeops.persistence.database import Database
from safeops.persistence.models import ExternalOperationRow, ExternalRecordRow
from safeops.tools.registry import Handler, ToolRegistry


async def seed_demo(db: Database) -> None:
    records = [
        {
            "id": "INV-10032",
            "customer_id": "CUS-001",
            "data": {"total_cents": 9000, "refunded_cents": 0, "paid": True, "currency": "USD"},
        },
        {
            "id": "INV-10077",
            "customer_id": "CUS-002",
            "data": {"total_cents": 24000, "refunded_cents": 0, "paid": True, "currency": "USD"},
        },
        {
            "id": "ACC-2041",
            "customer_id": "CUS-001",
            "data": {"plan": "pro", "status": "active", "locked": False, "reset_count": 0},
        },
        {
            "id": "ACC-2077",
            "customer_id": "CUS-002",
            "data": {"plan": "enterprise", "status": "active", "locked": False, "reset_count": 0},
        },
    ]
    async with db.sessions.begin() as session:
        for record in records:
            await session.execute(
                insert(ExternalRecordRow).values(**record).on_conflict_do_nothing()
            )


def local_handler(db: Database, tool: str, writes: bool) -> Handler:
    async def call(args: dict[str, Any], key: str, customer: str) -> dict[str, Any]:
        ref = args.get("invoice_ref") or args["account_ref"]
        async with db.sessions.begin() as session:
            record = await session.scalar(
                select(ExternalRecordRow).where(ExternalRecordRow.id == ref).with_for_update()
            )
            if record is None or record.customer_id != customer:
                raise ToolRefused("record not found for this customer")
            if writes:
                previous = await session.get(ExternalOperationRow, key)
                if previous:
                    return previous.result
            data = dict(record.data)
            if tool == "issue_refund":
                amount = args["amount_cents"]
                if not data["paid"] or data["refunded_cents"] + amount > data["total_cents"]:
                    raise ToolRefused("refund exceeds available paid balance")
                data["refunded_cents"] += amount
                result = {
                    "invoice_ref": ref,
                    "refunded_cents": amount,
                    "remaining_cents": data["total_cents"] - data["refunded_cents"],
                }
            elif tool == "reset_credentials":
                data["reset_count"] += 1
                result = {"account_ref": ref, "state": "reset_sent"}
            elif tool == "lock_account":
                data["locked"] = True
                result = {"account_ref": ref, "state": "locked"}
            elif tool == "cancel_subscription":
                data["status"] = "cancelled"
                result = {"account_ref": ref, "state": "cancelled"}
            elif tool == "change_plan":
                data["plan"] = args["plan"]
                result = {"account_ref": ref, "plan": args["plan"]}
            else:
                result = {"reference": ref, **data}
            if writes:
                result["external_operation_id"] = digest(key)
                record.data = data
                session.add(ExternalOperationRow(key=key, result=result))
            return result

    return call


def bind_local(db: Database, registry: ToolRegistry) -> None:
    for spec in list(registry.specs.values()):
        registry.bind(spec.name, local_handler(db, spec.name, spec.side_effect_type != "none"))
