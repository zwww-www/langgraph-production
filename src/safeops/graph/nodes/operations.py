from time import perf_counter

from langgraph.types import interrupt

from safeops.approvals.service import request_for
from safeops.domain.models import Action, Forbidden, Receipt, Ticket, ToolRefused, canonical
from safeops.graph.deps import Dependencies
from safeops.graph.state import AgentState, trail
from safeops.policy.models import ApprovalRequirement, PolicyDecision, RiskAssessment
from safeops.risk.outcomes import record_action
from safeops.tools.postconditions import verify_postcondition


async def assess(state: AgentState, deps: Dependencies) -> dict:
    action = Action.model_validate(state["action"]) if state.get("action") else None
    if action:
        policy = await deps.policy.assess(action)
        if not state.get("dry_run"):
            await record_action(
                deps.policy.db,
                state["run_id"],
                Ticket.model_validate(state["ticket"]),
                action,
                policy,
            )
    elif state["domain"] == "answer" and not state.get("error"):
        policy = PolicyDecision(
            decision="ALLOW",
            version=(await deps.policy.active()).revision,
            risk=RiskAssessment(score=0, level="low", reasons=["no business action"]),
        )
    else:
        policy = PolicyDecision(
            decision="ESCALATE",
            version=(await deps.policy.active()).revision,
            requirement=ApprovalRequirement(role="operator"),
            risk=RiskAssessment(score=90, level="high", reasons=["human judgment required"]),
        )
    await deps.events.emit(state["run_id"], "risk_assessed", "policy", policy.model_dump())
    return {
        "policy": policy.model_dump(),
        "trail": trail(state, "policy"),
        "outcome": "denied" if policy.decision == "DENY" else "pending",
    }


async def human_gate(state: AgentState, deps: Dependencies) -> dict:
    if state.get("dry_run"):
        return {
            "approval": {"decision": "approve", "simulated": True},
            "trail": trail(state, "human_gate"),
        }
    deps.faults.hit("before_approval")
    action = Action.model_validate(state["action"]) if state.get("action") else None
    request = request_for(
        state["run_id"],
        Ticket.model_validate(state["ticket"]),
        action,
        PolicyDecision.model_validate(state["policy"]),
    )
    await deps.approvals.request(request)
    await deps.events.emit(
        state["run_id"], "approval_requested", "human_gate", {"token": request.token}
    )
    raw = interrupt(request.model_dump(mode="json"))
    if not isinstance(raw, dict) or raw.get("token") != request.token:
        raise Forbidden("resume token mismatch")
    approval = await deps.approvals.verify(request)
    deps.faults.hit("after_approval")
    await deps.events.emit(state["run_id"], "approval_received", "human_gate", approval)
    return {
        "approval": approval,
        "trail": trail(state, "human_gate"),
        "outcome": "denied"
        if approval["decision"] == "reject"
        else "escalated"
        if not action
        else "pending",
    }


async def execute(state: AgentState, deps: Dependencies) -> dict:
    async with deps.policy.execution_guard():
        return await execute_authorized(state, deps)


async def execute_authorized(state: AgentState, deps: Dependencies) -> dict:
    action = Action.model_validate(state["action"])
    ticket = Ticket.model_validate(state["ticket"])
    # Defense in depth: the node independently enforces canonical identity and current policy.
    args = deps.registry.require(action.tool, state["domain"]).validate(action.args)
    if Action.build(ticket, action.tool, args, action.domain) != action:
        raise Forbidden("noncanonical action identity")
    current = await deps.policy.assess(action)
    if current != PolicyDecision.model_validate(state["policy"]):
        await deps.events.emit(
            state["run_id"],
            "runtime_policy_mismatch",
            "execute",
            {
                "active_revision": current.version,
                "approval_revision": state["policy"].get("version"),
            },
        )
        raise Forbidden("policy changed; create a new reviewed request")
    if current.decision not in ("ALLOW", "REQUIRE_APPROVAL"):
        raise Forbidden("policy denies execution")
    if current.decision == "REQUIRE_APPROVAL" and not state.get("dry_run"):
        verified = await deps.approvals.verify(
            request_for(state["run_id"], ticket, action, current)
        )
        if verified["decision"] != "approve":
            raise Forbidden("approval denied")
    started = perf_counter()
    await deps.events.emit(
        state["run_id"],
        "tool_started",
        "execute",
        {"tool": action.tool, "action_id": action.action_id},
    )
    try:
        receipt = await deps.effects.perform(
            state["run_id"], ticket.customer_id, action, state.get("dry_run", False)
        )
    except ToolRefused as exc:
        return {"outcome": "refused", "error": str(exc), "trail": trail(state, "execute")}
    await deps.events.emit(
        state["run_id"],
        "tool_completed",
        "execute",
        {
            "tool": action.tool,
            "action_id": action.action_id,
            "duration_ms": round((perf_counter() - started) * 1000),
            "replayed": receipt.replayed,
        },
    )
    return {
        "receipt": receipt.model_dump(mode="json"),
        "outcome": "executed",
        "trail": trail(state, "execute"),
    }


async def verify(state: AgentState, deps: Dependencies) -> dict:
    postcondition = {"verification_status": "unavailable", "reason": "no receipt"}
    if state.get("receipt"):
        receipt = Receipt.model_validate(state["receipt"])
        action = Action.model_validate(state["action"])
        if receipt.action_id != action.action_id or receipt.tool != action.tool:
            raise Forbidden("receipt does not match planned action")
        await deps.events.emit(
            state["run_id"], "receipt_verified", "verify", {"action_id": action.action_id}
        )
        postcondition = await verify_postcondition(
            deps.policy.db, deps.settings, state["run_id"], action, receipt
        )
    return {"trail": trail(state, "verify"), "postcondition": postcondition}


async def compose(state: AgentState, deps: Dependencies) -> dict:
    deps.faults.hit("before_compose")
    await deps.events.emit(state["run_id"], "compose_started", "compose")
    outcome = state.get("outcome", "answered")
    if outcome == "pending":
        outcome = "answered"
    if state.get("receipt"):
        receipt = Receipt.model_validate(state["receipt"])
        reply = (
            "Dry-run simulation: " if state.get("dry_run") else "Verified result: "
        ) + canonical(receipt.result)
    elif outcome in ("denied", "refused"):
        reply = "The operation was not performed. " + (
            state.get("error") or "The request was declined."
        )
    elif outcome == "escalated":
        reply = "This request has been handed to an operator for follow-up."
    else:
        reply = "I can help with invoice, account, and subscription operations. Include a record reference and the requested change."
    if not state.get("dry_run"):
        ticket = Ticket.model_validate(state["ticket"])
        result = (state.get("receipt") or {}).get("result", {})
        await deps.memory.remember(
            ticket.customer_id,
            ticket.id,
            "zh" if any("\u4e00" <= c <= "\u9fff" for c in ticket.text) else "en",
            result.get("plan"),
        )
    deps.faults.hit("after_compose")
    return {"reply": reply, "outcome": outcome, "trail": trail(state, "compose")}
