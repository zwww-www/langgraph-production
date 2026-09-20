from typing import Any

from langgraph.graph import END, START, StateGraph

from safeops.domain.models import Domain
from safeops.graph.deps import Dependencies
from safeops.graph.nodes import operations
from safeops.graph.routing import after_gate, after_policy, after_supervisor
from safeops.graph.state import AgentState
from safeops.graph.subgraphs.domain import build_domain
from safeops.graph.supervisor import supervise


def build_graph(deps: Dependencies, checkpointer: Any) -> Any:
    graph = StateGraph(AgentState)

    async def supervisor(state: AgentState) -> dict:
        return await supervise(state, deps)

    graph.add_node("supervisor", supervisor)
    domains: tuple[Domain, ...] = ("billing", "account", "subscription")
    for domain in domains:
        graph.add_node(domain, build_domain(domain, deps))
        graph.add_edge(domain, "policy")
    for name, function in (
        ("policy", operations.assess),
        ("human_gate", operations.human_gate),
        ("execute", operations.execute),
        ("verify", operations.verify),
        ("compose", operations.compose),
    ):

        def bind(fn: Any) -> Any:
            async def node(state: AgentState) -> dict:
                return await fn(state, deps)

            return node

        graph.add_node(name, bind(function))
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", after_supervisor, [*domains, "policy"])
    graph.add_conditional_edges("policy", after_policy, ["execute", "human_gate", "compose"])
    graph.add_conditional_edges("human_gate", after_gate, ["execute", "compose"])
    graph.add_edge("execute", "verify")
    graph.add_edge("verify", "compose")
    graph.add_edge("compose", END)
    return graph.compile(checkpointer=checkpointer)
