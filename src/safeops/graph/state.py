from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    ticket: dict[str, Any]
    memory: dict[str, Any]
    decision: dict[str, Any]
    domain: str
    plan: dict[str, Any] | None
    action: dict[str, Any] | None
    policy: dict[str, Any]
    approval: dict[str, Any] | None
    receipt: dict[str, Any] | None
    postcondition: dict[str, Any]
    outcome: str
    reply: str
    trail: list[str]
    audit_context: dict[str, Any]
    dry_run: bool
    error: str | None


def trail(state: AgentState, node: str) -> list[str]:
    # Single sequential branch; overwrite is intentional. A subgraph returns the full state.
    return [*state.get("trail", []), node]
