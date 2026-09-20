from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from safeops.approvals.service import request_for
from safeops.config import Settings
from safeops.domain.models import Action, Conflict, Forbidden, Principal, Ticket
from safeops.faults import CRASH_POINTS, Faults, SimulatedCrash
from safeops.graph.runtime import Runtime
from safeops.persistence.models import ExternalOperationRow
from safeops.policy.models import PolicyDecision
from safeops.tools.local import seed_demo

ADMIN = Principal(name="evaluation", role="admin")


def comparable(state: dict[str, Any]) -> dict[str, Any]:
    """Compare semantic state; namespace, wall-clock, audit and replay metadata are separate invariants."""
    result = dict((state.get("receipt") or {}).get("result", {}))
    result.pop("external_operation_id", None)
    return {key: state.get(key) for key in ("domain", "plan", "policy", "outcome", "trail")} | {
        "result": result
    }


async def settle(runtime: Runtime, run_id: str) -> dict[str, Any]:
    result = await runtime.process(run_id)
    if result["interrupts"]:
        await runtime.deps.approvals.decide(
            result["interrupts"][0]["token"], ADMIN, "approve", "evaluation"
        )
        result = await runtime.process(run_id)
    return result


async def evaluate_database(settings: Settings) -> dict[str, Any]:
    if settings.tool_bindings:
        raise ValueError("database evaluation requires local demo tools; external bindings refused")
    prefix = "EVAL-" + uuid4().hex
    rows: list[dict[str, Any]] = []
    async with Runtime(settings) as runtime:
        await seed_demo(runtime.db)
        await runtime.ticket(
            Ticket(id=prefix + "baseline", customer_id="CUS-001", text="Reset credentials ACC-2041")
        )
        baseline = comparable(
            (await settle(runtime, await runtime.enqueue(prefix + "baseline")))["values"]
        )
    for point in CRASH_POINTS:
        faults = Faults(point)
        async with Runtime(settings, faults=faults) as runtime:
            ticket = Ticket(
                id=prefix + point, customer_id="CUS-001", text="Reset credentials ACC-2041"
            )
            await runtime.ticket(ticket)
            run_id = await runtime.enqueue(ticket.id)
            try:
                await settle(runtime, run_id)
            except SimulatedCrash:
                pass
        # The whole runtime, engine, checkpointer and graph are reconstructed here.
        async with Runtime(settings) as fresh:
            result = await settle(fresh, run_id)
            status = (await fresh.get_run(run_id))["status"]
            async with fresh.db.sessions() as session:
                count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(ExternalOperationRow)
                        .where(ExternalOperationRow.key.startswith(run_id + ":"))
                    )
                    or 0
                )
            rows.append(
                {
                    "point": point,
                    "reached": faults.fired,
                    "in_doubt": status == "in_doubt",
                    "deterministic": status == "completed"
                    and comparable(result["values"]) == baseline,
                    "external_operations": count,
                }
            )
    invalid_accepted = 0
    conflict_accepted = 0
    async with Runtime(settings) as runtime:
        ticket = Ticket(
            id=prefix + "security", customer_id="CUS-001", text="Reset credentials ACC-2041"
        )
        await runtime.ticket(ticket)
        run_id = await runtime.enqueue(ticket.id)
        result = await runtime.process(run_id)
        token = result["interrupts"][0]["token"]
        try:
            await runtime.deps.approvals.decide(
                token, Principal(name="unprivileged", role="operator"), "approve", "bad"
            )
            invalid_accepted += 1
        except Forbidden:
            pass
        state = result["values"]
        forged = request_for(
            "wrong-thread",
            ticket,
            Action.model_validate(state["action"]),
            PolicyDecision.model_validate(state["policy"]),
        )
        try:
            await runtime.deps.approvals.verify(forged)
            invalid_accepted += 1
        except Forbidden:
            pass
        await runtime.deps.approvals.decide(token, ADMIN, "approve", "same")
        await runtime.deps.approvals.decide(token, ADMIN, "approve", "same")
        try:
            await runtime.deps.approvals.decide(token, ADMIN, "reject", "same")
            conflict_accepted += 1
        except Conflict:
            pass
        # Evaluate actual graph interruptions across every registered tool.
        required, missed, forbidden, fatigue = 0, 0, 0, 0
        from safeops.evaluation.fixtures import CASES

        for index, case in enumerate(CASES):
            ticket = Ticket(id=prefix + f"hitl-{index}", customer_id="CUS-001", text=case.text)
            await runtime.ticket(ticket)
            result = await runtime.process(await runtime.enqueue(ticket.id))
            stopped = bool(result["interrupts"])
            if case.policy in ("REQUIRE_APPROVAL", "ESCALATE"):
                required += 1
                missed += not stopped
            else:
                forbidden += 1
                fatigue += stopped
    reached = sum(r["reached"] for r in rows)
    deterministic = sum(r["deterministic"] for r in rows)
    doubt = sum(r["in_doubt"] for r in rows)
    duplicates = sum(max(0, r["external_operations"] - 1) for r in rows)
    scores = {
        "crash_point_reached_rate": reached / len(rows),
        "deterministic_resume_rate": deterministic / reached if reached else 0,
        "in_doubt_rate": doubt / reached if reached else 0,
        "duplicate_side_effect_count": duplicates,
        "invalid_approval_accepted_count": invalid_accepted,
        "conflicting_approval_replay_accepted_count": conflict_accepted,
        "interrupt_miss_rate": missed / required,
        "approval_fatigue_rate": fatigue / forbidden,
    }
    passed = (
        reached == len(rows)
        and deterministic + doubt == reached
        and deterministic >= 6
        and doubt == 3
    )
    passed = (
        passed and duplicates == invalid_accepted == conflict_accepted == missed == fatigue == 0
    )
    return {
        "passed": passed,
        "metrics": scores,
        "boundaries": rows,
        "comparison": "plan, policy, domain, outcome, trail and business result; excludes namespaces, timestamps, memory history and external operation IDs",
    }
