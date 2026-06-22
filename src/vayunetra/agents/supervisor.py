"""LangGraph supervisor agent for VayuNetra."""

from __future__ import annotations

from vayunetra.agents.nodes import (
    advisory_node,
    attribution_node,
    composer_node,
    enforce_node,
    forecast_node,
    general_node,
    router_node,
)
from vayunetra.agents.state import AgentState

try:
    from langgraph.graph import END, StateGraph

    graph_builder = StateGraph(AgentState)

    # Add nodes
    graph_builder.add_node("router", router_node)
    graph_builder.add_node("forecast", forecast_node)
    graph_builder.add_node("attribution", attribution_node)
    graph_builder.add_node("enforce", enforce_node)
    graph_builder.add_node("advisory", advisory_node)
    graph_builder.add_node("general", general_node)
    graph_builder.add_node("composer", composer_node)

    # Entry
    graph_builder.set_entry_point("router")

    # Conditional routing from router
    graph_builder.add_conditional_edges(
        "router",
        lambda s: s["intent"],
        {
            "forecast": "forecast",
            "attribution": "attribution",
            "enforce": "enforce",
            "advisory": "advisory",
            "general": "general",
        },
    )

    # All tool nodes → composer → END
    for node in ("forecast", "attribution", "enforce", "advisory", "general"):
        graph_builder.add_edge(node, "composer")
    graph_builder.add_edge("composer", END)

    graph = graph_builder.compile()

except ImportError:
    # Fallback: simple function-based approach
    def graph_invoke(state: AgentState) -> AgentState:
        state.update(router_node(state))
        intent = state["intent"]
        tool_fn = {
            "forecast": forecast_node,
            "attribution": attribution_node,
            "enforce": enforce_node,
            "advisory": advisory_node,
            "general": general_node,
        }[intent]
        state.update(tool_fn(state))
        state.update(composer_node(state))
        return state

    class _FallbackGraph:
        def invoke(self, state: AgentState) -> AgentState:
            return graph_invoke(state)

    graph = _FallbackGraph()
