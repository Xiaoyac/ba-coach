"""Graph state and runtime context.

`AgentState` is the payload that traverses the DAG — every node reads from it
and returns a partial update. `GraphContext` is the *runtime* half: live
objects (provider client, session store, knowledge base) that must not live in
state because they are neither serialisable nor part of the turn's data.
LangGraph injects it via `Runtime[GraphContext]`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from ..config import Settings
from ..generation_control import GenerationControl
from ..memos_integration import MemosIntegrationManager
from ..providers.base import LLMProvider
from ..retrieval import KnowledgeBase, KnowledgeChunk
from ..schemas import Message
from ..session import SessionStore


class AgentState(TypedDict, total=False):
    """State threaded through the graph.

    `total=False` so nodes can return partial updates; LangGraph merges each
    returned dict into the running state.
    """

    # ---- Input, set by the caller ------------------------------------
    session_id: str
    user_input: str
    metadata: dict[str, str]
    # Pin a module and bypass analyze_intent_node entirely.
    forced_module: str | None
    # Authenticated profile UUID resolved from the bearer session (see
    # app.identity), or None for an anonymous chat call. This — not session_id
    # — is the key Memos long-term memory is scoped by: a session is one
    # conversation, a subject is one person across all of them.
    subject_id: str | None

    # ---- Pre-processing ----------------------------------------------
    # Prior turns, loaded by extract_memory_node.
    chat_history: list[Message]
    # Durable facts carried across sessions, loaded by extract_memory_node.
    memory: dict[str, str]
    # The module persisted on the session from the *last* turn's
    # route_next_module_node decision (or None for a brand-new session),
    # loaded by extract_memory_node. This — not a per-message guess — is what
    # analyze_intent_node uses to pick this turn's module.
    current_module: str | None
    # Module chosen by analyze_intent_node: "module_1".."module_4".
    extracted_intent: str
    # How it was chosen: explicit | sticky | default.
    routed_by: str
    # Recent Memos entries for this subject, loaded by recall_memory_node —
    # only on this session's first turn or the turn it first enters module 4
    # (see that node for why). Empty otherwise; always present once
    # recall_memory_node has run.
    long_term_memory: list[str]
    # Facts already established in the *business* tables — chiefly the module-2
    # PA card, which later modules reference but never hold their own copy of.
    # Loaded by recall_memory_node alongside long-term memory.
    clinical_context: list[str]
    # The subject's own registration profile (nickname, tone preference,
    # physical limits, movement taboos). Unlike the two above this is loaded
    # on *every* turn — see recall_memory_node on why a safety constraint
    # cannot be gated to the turns where it is cheap.
    profile_context: list[str]
    # Explicit conversation-scoped sub-step state from the database.
    module_steps: dict[str, list[str]]
    active_cycle_id: str | None

    # ---- Risk gate ---------------------------------------------------
    # Set by risk_gate_node: the coerced risk record when this turn was
    # flagged, None otherwise. Truthy here means the module branch is skipped
    # entirely and `crisis_node` answers instead.
    risk: dict | None

    # ---- Module branch -----------------------------------------------
    retrieved_knowledge: list[KnowledgeChunk]

    # ---- Post-processing -----------------------------------------------
    # Set by route_next_module_node: which module should handle the *next*
    # turn. Persisted onto the session by update_memory_and_format_node —
    # this turn's own response is already driven by extracted_intent above,
    # this only ever affects the turn after it.
    next_module: str
    # Post-hoc Router Agent thought trace and the exact router model. This is
    # separate from the main reply reasoning above it in the disclosure UI.
    routing_reasoning_content: str
    router_model_name: str
    # True only for an ordinary successful turn whose Router Agent will run
    # after the visible response has already completed.
    routing_pending: bool

    # ---- Output -------------------------------------------------------
    final_response: str
    # Provider-supplied thinking for this turn. Transported and persisted
    # separately so it can be disclosed on demand without becoming the reply.
    reasoning_content: str
    provider: str
    model: str
    usage: dict[str, int]
    # Operational metrics for this turn. Persisted on the assistant message;
    # kept out of the user-facing prompt and transcript.
    telemetry: dict[str, Any]
    # Set when the module branch failed. The graph still converges on the
    # post-processing node so a partial answer is persisted and the caller
    # gets a structured error rather than a dropped connection.
    error: str | None


@dataclass
class GraphContext:
    """Live dependencies for one graph run."""

    provider: LLMProvider
    # The conversational provider follows the user's preference; the module
    # router is deliberately pinned to DeepSeek and never follows that choice.
    router_provider: LLMProvider
    store: SessionStore
    knowledge_base: KnowledgeBase
    settings: Settings
    # None when MEMOS_BASE_URL / MEMOS_API_KEY aren't configured — every call
    # site treats that the same as "no long-term memory available" rather
    # than erroring.
    memos: MemosIntegrationManager | None = None
    # Session *factory*, not a session. The clinical writes it feeds run in
    # detached background tasks that outlive the request, so they have to open
    # their own connection — the request's session is back in the pool by then.
    # None disables clinical persistence entirely (the test graph, and any
    # caller with no database).
    sessionmaker: Any | None = None
    # Effective system-wide module-router prompt, loaded from the shared admin
    # override table for each request. None keeps direct graph callers and
    # tests on the source-controlled default.
    router_prompt: str | None = None
    # True for the SSE endpoint: module nodes stream token deltas through the
    # graph's custom stream. False for the JSON endpoint, which uses the
    # one-shot completion call so it gets a usage report back.
    stream: bool = False
    # An isolated evaluation can freeze effective prompts without enabling DB side effects.
    prompt_snapshot: dict[str, str] | None = None
    generation: GenerationControl | None = None
