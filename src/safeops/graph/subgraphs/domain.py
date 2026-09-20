from time import perf_counter
from typing import Any

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from safeops.domain.models import Action, Domain, Ticket, ToolCall, ToolRefused, canonical
from safeops.graph.deps import Dependencies
from safeops.graph.state import AgentState, trail
from safeops.llm.base import LLMRequest

DOMAIN_RULES: dict[Domain, str] = {
    "billing": "Investigate owned invoices/payments. Refund only explicit amounts; never infer full balance.",
    "account": "Operate only on the named customer account. Never request or return passwords or reset tokens.",
    "subscription": "Require an explicit target plan for changes. Hypothetical cancellation questions are not actions.",
}


def build_domain(domain: Domain, deps: Dependencies) -> Any:
    async def plan(state: AgentState) -> dict:
        deps.faults.hit("before_plan")
        await deps.events.emit(state["run_id"], "subgraph_entered", domain)
        prompt = (
            DOMAIN_RULES[domain] + '\nReturn only JSON {"tool":"name","args":{}}. '
            "Do not invent identifiers. Use only these tools: "
            + canonical(deps.registry.catalogue(domain))
        )
        started = perf_counter()
        response = await deps.provider.complete(
            LLMRequest(tag=domain, system=prompt, user=state["ticket"]["text"])
        )
        try:
            call = ToolCall.model_validate_json(response.text)
            spec = deps.registry.require(call.tool, domain)
            args = spec.validate(call.args)
            # Reference grounding complements schema validation; ownership checked downstream.
            for name, value in args.items():
                if name.endswith("_ref") and value not in state["ticket"]["text"]:
                    raise ToolRefused("ungrounded reference")
            action = Action.build(Ticket.model_validate(state["ticket"]), call.tool, args, domain)
            update: dict[str, Any] = {
                "plan": {"tool": call.tool, "args": args},
                "action": action.model_dump(),
                "error": None,
            }
        except (ValidationError, ToolRefused, ValueError):
            update = {"plan": None, "action": None, "error": "invalid domain plan"}
        await deps.events.emit(
            state["run_id"],
            "plan_created",
            domain,
            {
                "valid": update["action"] is not None,
                "duration_ms": round((perf_counter() - started) * 1000),
                "model": response.model,
                "usage": response.usage,
            },
        )
        deps.faults.hit("after_plan")
        return {**update, "trail": trail(state, domain + ":plan")}

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", END)
    return graph.compile()
