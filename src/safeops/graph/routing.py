from safeops.graph.state import AgentState


def after_supervisor(state: AgentState) -> str:
    route = state.get("decision", {}).get("route", "escalate")
    return route if route in ("billing", "account", "subscription") else "policy"


def after_policy(state: AgentState) -> str:
    decision = state["policy"]["decision"]
    if decision == "ALLOW":
        return "execute" if state.get("action") else "compose"
    if decision in ("REQUIRE_APPROVAL", "ESCALATE"):
        return "human_gate"
    return "compose"


def after_gate(state: AgentState) -> str:
    return (
        "execute"
        if state.get("action") and (state.get("approval") or {}).get("decision") == "approve"
        else "compose"
    )
