"""LangGraph workflow for the psychology assistant.

    START
      -> extract_memory            fetch history + durable memory + current_module
      -> analyze_intent            read back this turn's module (state, not a guess)
      -> recall_memory             Memos recall — only first turn / entering module 4
      -> module_1 | module_2 | module_3 | module_4
                                   retrieve knowledge, run the module prompt
      -> route_next_module         mark ordinary post-hoc routing as pending
      -> summarizer                persist risk-only after-turn work
      -> update_memory_and_format  persist the turn, update memory, publish
      -> END

After the visible reply is persisted, ``schedule_background_routing`` runs the
DeepSeek Router Agent, enriches that assistant row, advances durable state and
dispatches transition-only extraction/Memos work. The HTTP response never
waits for that model call.

LangGraph handles orchestration and state only. Model calls go through
`app.providers`, which uses the official Anthropic and OpenAI SDKs directly —
so prompt-cache breakpoints and provider-specific parameters stay under our
control rather than behind a LangChain chat-model wrapper.
"""

from .builder import build_graph, get_graph
from .nodes import (
    MODULE_NODES,
    analyze_intent_node,
    extract_memory_node,
    recall_memory_node,
    route_after_intent,
    route_next_module_node,
    schedule_background_routing,
    summarizer_node,
    update_memory_and_format_node,
    wait_for_pending_routing,
)
from .state import AgentState, GraphContext

__all__ = [
    "AgentState",
    "GraphContext",
    "MODULE_NODES",
    "analyze_intent_node",
    "build_graph",
    "extract_memory_node",
    "get_graph",
    "recall_memory_node",
    "route_after_intent",
    "route_next_module_node",
    "schedule_background_routing",
    "summarizer_node",
    "update_memory_and_format_node",
    "wait_for_pending_routing",
]
