"""Graph construction and compilation.

    START -> extract_memory -> analyze_intent -> recall_memory -> risk_gate
          -> crisis | [module_1..4]
          -> route_next_module (marks delayed routing) -> summarizer
          -> update_memory_and_format -> END

`risk_gate` is the one node allowed to add latency to a turn: it decides
whether the person gets the coaching reply at all, so it cannot run after it.
When it fires, `crisis` answers instead of the module and the state machine
holds its position — a crisis turn never advances a module.

The graph is compiled once at import and reused for every request. It holds no
per-request state: everything mutable travels in `AgentState`, and live
dependencies arrive per-run through `Runtime[GraphContext]`.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from ..prompts import MODULE_PROMPTS
from .nodes import (
    MODULE_NODES,
    analyze_intent_node,
    crisis_node,
    extract_memory_node,
    recall_memory_node,
    risk_gate_node,
    route_after_risk,
    route_next_module_node,
    summarizer_node,
    update_memory_and_format_node,
)
from .state import AgentState, GraphContext

RISK_NODE = "risk_gate"
CRISIS_NODE = "crisis"
ROUTE_NODE = "route_next_module"
SUMMARIZER_NODE = "summarizer"
POST_NODE = "update_memory_and_format"


def build_graph() -> StateGraph:
    """Wire the DAG. Returns the uncompiled builder so tests can inspect it."""
    builder: StateGraph = StateGraph(AgentState, context_schema=GraphContext)

    # --- Pre-processing (linear) -------------------------------------
    builder.add_node("extract_memory", extract_memory_node)
    builder.add_node("analyze_intent", analyze_intent_node)
    builder.add_node("recall_memory", recall_memory_node)
    builder.add_node(RISK_NODE, risk_gate_node)

    # --- Module branches, plus the crisis branch that can replace them ---
    for module_name, node in MODULE_NODES.items():
        builder.add_node(module_name, node)
    builder.add_node(CRISIS_NODE, crisis_node)

    # --- Delayed-routing marker + post-processing (convergence) --------
    builder.add_node(ROUTE_NODE, route_next_module_node)
    builder.add_node(SUMMARIZER_NODE, summarizer_node)
    builder.add_node(POST_NODE, update_memory_and_format_node)

    builder.add_edge(START, "extract_memory")
    builder.add_edge("extract_memory", "analyze_intent")
    builder.add_edge("analyze_intent", "recall_memory")
    builder.add_edge("recall_memory", RISK_NODE)

    # Conditional fan-out. The explicit path_map keeps the mapping between a
    # routing decision and a node name declarative, and makes LangGraph fail at
    # build time if a module has no node rather than at request time. `crisis`
    # is one more destination in the same map — the risk gate's verdict and the
    # intent decision are resolved together in `route_after_risk`, so there is
    # exactly one place that decides who answers this turn.
    builder.add_conditional_edges(
        RISK_NODE,
        route_after_risk,
        {
            **{module_name: module_name for module_name in MODULE_PROMPTS},
            CRISIS_NODE: CRISIS_NODE,
        },
    )

    # Fan-in: every branch converges on the cheap routing marker. The actual
    # DeepSeek Router Agent is scheduled by the HTTP layer only after the
    # assistant row is durable, so it can enrich that exact row asynchronously.
    for module_name in MODULE_PROMPTS:
        builder.add_edge(module_name, ROUTE_NODE)
    # The crisis branch converges too, so the turn is still persisted and
    # summarised — but route_next_module_node holds position on a crisis turn
    # rather than advancing the programme.
    builder.add_edge(CRISIS_NODE, ROUTE_NODE)
    builder.add_edge(ROUTE_NODE, SUMMARIZER_NODE)
    builder.add_edge(SUMMARIZER_NODE, POST_NODE)

    builder.add_edge(POST_NODE, END)
    return builder


@lru_cache(maxsize=1)
def get_graph():
    """The compiled graph. Cached — compile once, reuse across requests."""
    return build_graph().compile(name="psychology-workflow")
