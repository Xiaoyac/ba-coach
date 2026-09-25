"""Compile the reply pipeline.

Load context -> safety screening -> current-turn router -> verified business
commit -> selected module reply -> background-extraction marker -> persistence.
A crisis turn bypasses the router and business transitions entirely.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from ..prompts import MODULE_PROMPTS
from ..pre_reply_routing import pre_reply_router_node, route_after_pre_reply, route_to_pre_reply
from .nodes import (
    MODULE_NODES,
    analyze_intent_node,
    crisis_node,
    extract_memory_node,
    recall_memory_node,
    risk_gate_node,
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
    builder.add_node("pre_reply_router", pre_reply_router_node)

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

    # Only a clean risk result may reach the current-turn decision/commit.
    builder.add_conditional_edges(
        RISK_NODE,
        route_to_pre_reply,
        {"pre_reply_router": "pre_reply_router", CRISIS_NODE: CRISIS_NODE},
    )
    builder.add_conditional_edges(
        "pre_reply_router",
        route_after_pre_reply,
        {
            **{module_name: module_name for module_name in MODULE_PROMPTS},
        },
    )

    # Replies converge on the marker for evidence extraction, not a second
    # model router. The HTTP layer schedules it once the assistant is durable.
    for module_name in MODULE_PROMPTS:
        builder.add_edge(module_name, ROUTE_NODE)
    # Crisis replies are still persisted, with progression held.
    builder.add_edge(CRISIS_NODE, ROUTE_NODE)
    builder.add_edge(ROUTE_NODE, SUMMARIZER_NODE)
    builder.add_edge(SUMMARIZER_NODE, POST_NODE)

    builder.add_edge(POST_NODE, END)
    return builder


@lru_cache(maxsize=1)
def get_graph():
    """The compiled graph. Cached — compile once, reuse across requests."""
    return build_graph().compile(name="psychology-workflow")
