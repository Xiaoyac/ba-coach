"""Graph nodes.

Shape of the DAG:

    START
      -> extract_memory_node          (linear pre-processing)
      -> analyze_intent_node          (reads back this turn's module)
      -> recall_memory_node           (long-term + clinical context)
      -> risk_gate_node               (screens *before* answering)
      -> crisis_node | module_1..4    (conditional branch)
      -> route_next_module_node       (post-hoc: decides the *next* module)
      -> summarizer_node              (dispatches all after-the-turn work)
      -> update_memory_and_format_node               (convergence)
      -> END

Every node returns a *partial* state update, which LangGraph merges into
`AgentState`. Nodes emit progress through LangGraph's custom stream
(`get_stream_writer`); the HTTP layer turns those events into SSE frames.
Nodes never write SSE themselves — that keeps the graph transport-agnostic
and directly unit-testable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from time import perf_counter

from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime

from ..ai_telemetry import save_ai_event
from ..clinical_extraction import assess_risk_detailed, extract_module_record_detailed
from ..clinical_store import (
    coerce,
    load_clinical_context,
    load_profile_context,
    persist_module_record,
    persist_risk,
)
from ..clinical_fields import MODULE_SPECS, RISK_SPECS
from ..conversation_store import complete_background_routing
from ..memos_integration import MemosIntegrationManager
from ..prompt_store import effective_prompt_pair, effective_mediator_prompt
from ..knowledge_mediator import MEDIATOR_PROMPT, mediate_knowledge
from ..prompts import (
    CRISIS_PROMPT,
    CRISIS_RESOURCES,
    DEFAULT_MODULE,
    MODULE_PROMPTS,
    SystemPromptSegment,
    build_system_segments,
)
from ..providers.base import LLMProvider, ProviderError, as_text
from ..reasoning import ThinkingTagStreamGuard, normalize_reasoning_channels
from ..retrieval import DatabaseKnowledgeBase
from ..retrieval_intent import decide_retrieval
from ..router_agent import decide_target_module_with_reasoning, extract_pa_card
from ..schemas import Message
from ..workflow_state import (
    apply_router_decision,
    load_conversation_workflow,
    record_interaction_transition,
)
from .state import AgentState, GraphContext

logger = logging.getLogger(__name__)
from ..workflow_contract import VERSION as WORKFLOW_CONTRACT_VERSION

MEMORY_EXCERPT_CHARS = 200
ROUTER_TRANSCRIPT_CHARS = 16_000

_MODULE_NUMBER = {
    "module_1": "一",
    "module_2": "二",
    "module_3": "三",
    "module_4": "四",
}
_MODULE_STATUS_QUESTION = re.compile(
    r"(?:现在|当前|目前)?(?:是|在|处于|进行到)?(?:第)?(?:几|哪个|什么)(?:个)?模块"
    r"|(?:现在|当前|目前)?(?:是|在|处于|进行到)?模块(?:是)?(?:几|哪个|什么)"
    r"|(?:现在|当前|目前)(?:进行到|处于|在)(?:第)?(?:几|哪个|什么)(?:个)?(?:模块|阶段)"
    r"|(?:现在|当前|目前)(?:是|在|处于)(?:第)?(?:几|哪个|什么)(?:个)?阶段",
    re.IGNORECASE,
)


def authoritative_module_status_reply(user_input: str, module: str) -> str | None:
    """Answer direct workflow-state questions without asking an LLM to guess."""
    compact = re.sub(r"\s+", "", user_input)
    if len(compact) > 80 or not _MODULE_STATUS_QUESTION.search(compact):
        return None
    number = _MODULE_NUMBER.get(module)
    if number is None:
        return None
    return (
        f"当前是模块{number}（{module}），本条回复也由模块{number}处理。"
        "本轮结束后是否进入下一模块，会由路由 Agent 根据完整对话另行判断。"
    )


def unwrap_chat_reply(text: str) -> str:
    """Hide an internal ``{"chat_reply": ...}`` envelope from end users.

    Some administrator-authored prompts ask the model for structured JSON.
    That structure is useful internally, but the conversation transcript is a
    text surface. Accept a complete JSON object and also recover the string
    from a truncated object after an upstream streaming failure; never expose
    the field name itself.
    """

    stripped = text.strip()
    candidate = stripped
    if candidate.startswith("```json") and candidate.endswith("```"):
        candidate = candidate[7:-3].strip()
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("chat_reply"), str):
        return parsed["chat_reply"].strip()

    partial = re.match(
        r'^\s*(?:```json\s*)?\{\s*"chat_reply"\s*:\s*"(.*)',
        stripped,
        flags=re.DOTALL,
    )
    if partial:
        value = partial.group(1)
        value = re.sub(r'"\s*\}\s*(?:```)?\s*$', "", value)
        try:
            return json.loads(f'"{value}"').strip()
        except (json.JSONDecodeError, TypeError):
            return value.replace(r"\n", "\n").replace(r'\"', '"').strip()
    return text


@dataclass
class VisibleReplyBuffer:
    """Delay only possible JSON envelopes; ordinary prose still streams."""

    raw_parts: list[str]
    buffered_parts: list[str]
    mode: str = "deciding"  # deciding | holding | passthrough

    @classmethod
    def create(cls) -> "VisibleReplyBuffer":
        return cls(raw_parts=[], buffered_parts=[])

    def push(self, delta: str) -> list[str]:
        self.raw_parts.append(delta)
        if self.mode == "passthrough":
            return [delta]
        self.buffered_parts.append(delta)
        preview = "".join(self.buffered_parts).lstrip().lower()
        if not preview:
            return []
        structured_prefixes = ("{", "```json")
        if any(prefix.startswith(preview) for prefix in structured_prefixes):
            return []
        if preview.startswith(structured_prefixes):
            self.mode = "holding"
            return []
        self.mode = "passthrough"
        visible = "".join(self.buffered_parts)
        self.buffered_parts.clear()
        return [visible]

    def finish(self) -> tuple[str, list[str]]:
        raw = "".join(self.raw_parts)
        visible = unwrap_chat_reply(raw)
        if self.mode != "passthrough":
            return visible, [visible] if visible else []
        return visible, []


def _finish_stream_channels(
    content_guard: ThinkingTagStreamGuard,
    reply_buffer: VisibleReplyBuffer,
    reasoning_parts: list[str],
) -> tuple[str, str, list[str]]:
    """Finalize streamed provider channels without exposing protocol tags."""

    blocked = content_guard.mode == "blocked"
    pending_deltas: list[str] = []
    for safe_delta in content_guard.finish_passthrough():
        pending_deltas.extend(reply_buffer.push(safe_delta))
    blocked = blocked or content_guard.mode == "blocked"

    buffered_reply, final_deltas = reply_buffer.finish()
    normalized = normalize_reasoning_channels(
        content_guard.raw if blocked else buffered_reply,
        "".join(reasoning_parts),
    )
    if blocked:
        # A truncated tag that cannot be repaired is safer as an empty reply
        # than as a leaked scratchpad.
        safe_reply = normalized.reply if normalized.repaired else ""
        return safe_reply, normalized.reasoning, [safe_reply] if safe_reply else []

    # pending_deltas only occurs when a tiny ordinary prefix remained buffered
    # until EOF.  JSON envelope deltas come from VisibleReplyBuffer.finish().
    return normalized.reply, normalized.reasoning, pending_deltas + final_deltas


def _emit(event: dict) -> None:
    """Publish an event on the graph's custom stream.

    A no-op when nothing is consuming the stream (e.g. `ainvoke`), so nodes
    can emit unconditionally.
    """
    try:
        get_stream_writer()(event)
    except Exception:  # noqa: BLE001 — telemetry must never break a turn
        logger.debug("stream writer unavailable", exc_info=True)


# ---------------------------------------------------------------------------
# Step 2 — pre-processing
# ---------------------------------------------------------------------------


async def extract_memory_node(
    state: AgentState, runtime: Runtime[GraphContext]
) -> dict:
    """Load prior context for this session: chat history + durable memory.

    Reads through the session store, so swapping the store for Redis gives the
    graph cross-process memory with no change here.
    """
    context = runtime.context
    session = await context.store.get_or_create(state.get("session_id"))

    _emit(
        {
            "type": "trace",
            "node": "extract_memory",
            "detail": {
                "session_id": session.session_id,
                "history_messages": len(session.messages),
                "memory_keys": sorted(session.memory),
            },
        }
    )

    module_steps: dict[str, list[str]] = {}
    active_cycle_id: str | None = None
    if context.sessionmaker is not None:
        try:
            module_steps, active_cycle_id = await load_conversation_workflow(
                context.sessionmaker, session_id=session.session_id
            )
        except Exception:  # noqa: BLE001 — enrichment must not break chat
            logger.exception("loading workflow progress failed for %s", session.session_id)

    return {
        "session_id": session.session_id,
        "chat_history": list(session.messages),
        "memory": dict(session.memory),
        # Session metadata accumulates across turns; the request's own metadata
        # was merged into it by the route before the graph ran.
        "metadata": dict(session.metadata),
        # What route_next_module_node decided at the end of the last turn —
        # None for a session that has never completed a turn yet.
        "current_module": session.module,
        "module_steps": module_steps,
        "active_cycle_id": active_cycle_id,
    }


async def analyze_intent_node(
    state: AgentState, runtime: Runtime[GraphContext]
) -> dict:
    """Decide which module handles this turn.

    Precedence: explicit pin > the module route_next_module_node decided at
    the end of the *previous* turn (see router_agent.py) > default.

    This is deliberately not a per-message guess any more — no keyword
    heuristic, no generic "which module fits this text" classifier. The
    program's actual state machine (BA education must finish before goal
    setting, a PA card is required before modules 3/4, module 1 is never
    re-entered, …) is enforced once, post-hoc, by the router agent; a
    pre-turn classifier with no knowledge of those rules could — and
    reliably would, on the right keywords — route somewhere the rules
    forbid. Reading the persisted decision back is what keeps this node and
    that one from disagreeing with each other.
    """

    def decided(module: str, routed_by: str) -> dict:
        _emit(
            {
                "type": "meta",
                "node": "analyze_intent",
                "reply_module": module,
                "routed_by": routed_by,
            }
        )
        return {"extracted_intent": module, "routed_by": routed_by}

    forced = state.get("forced_module")
    if forced:
        # Validated at the API boundary; assert here so a bad programmatic
        # caller fails loudly instead of silently routing somewhere else.
        if forced not in MODULE_PROMPTS:
            raise ValueError(f"Unknown module {forced!r}")
        return decided(forced, "explicit")

    current = state.get("current_module")
    if current in MODULE_PROMPTS:
        return decided(current, "sticky")
    return decided(DEFAULT_MODULE, "default")


def route_after_intent(state: AgentState) -> str:
    """Conditional edge: map the routing decision onto a module node."""
    module = state.get("extracted_intent") or DEFAULT_MODULE
    return module if module in MODULE_PROMPTS else DEFAULT_MODULE


async def recall_memory_node(
    state: AgentState, runtime: Runtime[GraphContext]
) -> dict:
    """Pull recent Memos entries into this turn's prompt — but not every turn.

    Long-term memory only earns its place on two turns: the session's first
    (nothing in `chat_history` yet, so there is no in-session context to lean
    on) and the turn that first lands in module 4 (a fresh ABC review that
    needs the module 2 goal card's context, not just this session's own
    history). Every other turn already has what it needs in `chat_history` /
    `memory`, and re-fetching Memos there would just add latency and stale
    the currently-relevant module content with older material.

    "First turn in module 4" is read off `memory["last_module"]`
    (`_derive_memory` writes it every turn) rather than off `current_module`:
    by the time this node runs, `current_module`/`extracted_intent` has
    already been stickily set to module_4 for this turn, so it can't tell
    "just arrived" from "already here". `last_module` still holds the module
    from the turn *before* this one, which can.
    """
    subject_id = state.get("subject_id")
    memos = runtime.context.memos
    sessionmaker = runtime.context.sessionmaker
    empty: dict = {"long_term_memory": [], "clinical_context": [], "profile_context": []}

    if not subject_id:
        return empty

    # The registration profile is loaded on EVERY turn, ahead of the gate
    # below. It is not "context" that earns its cost on some turns: a physical
    # limitation bounds every activity the coach may propose, so a coach that
    # honours it only on turn one is worse than one that never knew. One
    # indexed lookup by uuid.
    profile: list[str] = []
    if sessionmaker is not None:
        try:
            profile = await load_profile_context(sessionmaker, user_id=subject_id)
        except Exception:  # noqa: BLE001 — never fail a turn over context loading
            logger.exception("loading profile failed for %s", subject_id[:8])

    # A server-owned opening is now the first assistant message, so "first
    # turn" means no prior *user* message rather than an empty transcript.
    # Otherwise creating the opening would accidentally disable the very
    # first long-term-memory/profile recall.
    is_first_turn = not any(
        message.role == "user" for message in (state.get("chat_history") or [])
    )
    entering_module_4 = state.get("extracted_intent") == "module_4" and (
        state.get("memory") or {}
    ).get("last_module") != "module_4"

    from ..v2_profile import enabled as v2_enabled
    if v2_enabled():
        memos = None
    if not (is_first_turn or entering_module_4 or v2_enabled()):
        return {**empty, "profile_context": profile}

    memories = (
        await memos.retrieve_recent_memos(
            subject_id,
            query=state.get("user_input", ""),
        )
        if memos
        else []
    )

    # Clinical context is fetched on the same two turns as long-term memory,
    # for the same reason — and it is what makes module 4 able to review a
    # goal card module 2 created rather than asking for it again. Unlike the
    # Memos call this reads the business tables directly, so a failure here is
    # a database problem worth logging rather than a missing integration.
    clinical: list[str] = []
    if sessionmaker is not None:
        try:
            clinical = await load_clinical_context(
                sessionmaker,
                user_id=subject_id,
                session_id=state.get("session_id"),
            )
        except Exception:  # noqa: BLE001 — never fail a turn over context loading
            logger.exception("loading clinical context failed for %s", subject_id[:8])

    _emit(
        {
            "type": "trace",
            "node": "recall_memory",
            "detail": {
                "reason": "first_turn" if is_first_turn else "entering_module_4",
                "count": len(memories),
                "clinical": len(clinical),
                "profile": len(profile),
            },
        }
    )
    return {
        "long_term_memory": memories,
        "clinical_context": clinical,
        "profile_context": profile,
    }


# ---------------------------------------------------------------------------
# Step 3 — the four module branches
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleConfig:
    """Per-module knobs. Give each branch its own retrieval budget."""

    top_k: int = 3
    retrieve: bool = True


MODULE_CONFIGS: dict[str, ModuleConfig] = {
    "module_1": ModuleConfig(top_k=2),
    # BA + PA + MI can all be relevant while selecting a goal. Four slots
    # leave room for both a PA evidence chunk and a concrete Compendium row.
    "module_2": ModuleConfig(top_k=4),
    # BA + BCT/barrier knowledge + MI can all be relevant while preparing
    # execution and responding to resistance around the recording contract.
    "module_3": ModuleConfig(top_k=4),
    "module_4": ModuleConfig(top_k=2),
}


def _knowledge_query(state: AgentState) -> str:
    """Build retrieval context without sending the whole transcript to search."""
    pieces = [state["user_input"]]
    for message in reversed(state.get("chat_history") or []):
        if message.role != "user" or message.content in pieces:
            continue
        pieces.append(message.content)
        if len(pieces) == 3:
            break
    pa_card = (state.get("memory") or {}).get("pa_card")
    if pa_card:
        pieces.append(pa_card)
    return "\n".join(pieces)[:6000]


def make_module_node(module_name: str, config: ModuleConfig):
    """Build one module node: retrieve knowledge, then run the module prompt.

    The four branches share this sub-chain shape (retrieve -> compile prompt ->
    call the model), so they are generated rather than copy-pasted. Each is
    registered under its own node name and gets its own `ModuleConfig`. When a
    branch outgrows the shared shape, write a bespoke async function and
    register that instead — the graph wiring in builder.py does not care.
    """

    async def module_node(state: AgentState, runtime: Runtime[GraphContext]) -> dict:
        from ..answer_validator import validate_answer, SAFE_REPLY
        from ..reply_workflow import read_reply_workflow, workflow_prompt, truthful_workflow_reply
        authoritative = (runtime.context.settings.database_schema_version == "v2" and
                         bool(state.get("subject_id")) and not (state.get("memory") or {}).get("sandbox_mode"))
        async def reply_authority():
            if not authoritative:
                return None
            try:
                return await read_reply_workflow(runtime.context.sessionmaker, state['subject_id'], state['session_id'])
            except Exception:
                return {"available": False}
        pending_output: list[dict] = []
        def emit_output(event):
            if runtime.context.settings.answer_validator_enabled or authoritative:
                pending_output.append(event)
            else:
                _emit(event)
        context = runtime.context
        user_input = state["user_input"]

        if (context.settings.database_schema_version == "v2" and module_name != "module_1"
                and context.sessionmaker is not None and not (state.get("memory") or {}).get("sandbox_mode")):
            from ..v2_workflow import runtime_for
            async with context.sessionmaker() as db:
                _, program = await runtime_for(db, state["session_id"])
            blocked_reply = None
            if program and program["flow_status"] == "completed":
                blocked_reply = "这次目标的记录已经结束并保留。如果想继续其他目标或讨论新方向，请新建一段聊天。"
            if program and program["flow_status"] == "paused":
                blocked_reply = "这个目标已暂停，历史记录仍保留。想继续时，可以新建聊天并选择“恢复并继续”这个目标，也可以讨论其他方向，不需要现在决定。"
            if blocked_reply:
                if context.stream:
                    _emit({"type": "delta", "text": blocked_reply})
                return {"retrieved_knowledge": [], "provider": "workflow_state", "model": "BA Coach 状态机",
                        "final_response": blocked_reply, "reasoning_content": "", "usage": {},
                        "telemetry": {"main_generation_duration_ms": 0}, "error": None}

        # Module identity is server-owned state, not a coaching judgement.
        # Answering this through a probabilistic model is exactly how the UI
        # badge and the prose reply can disagree even when the graph is right.
        status_reply = authoritative_module_status_reply(user_input, module_name)
        if status_reply is not None:
            if context.stream:
                _emit({"type": "delta", "text": status_reply})
            return {
                "retrieved_knowledge": [],
                "provider": "workflow_state",
                "model": "BA Coach 状态机",
                "final_response": status_reply,
                "reasoning_content": "",
                "usage": {},
                "telemetry": {"main_generation_duration_ms": 0},
                "error": None,
            }

        knowledge = []
        retrieval_metrics = {}
        if config.retrieve:
            gate_started = perf_counter()
            decision = decide_retrieval(
                state, enabled=context.settings.knowledge_intent_gate_enabled
            )
            gate_ms = (perf_counter() - gate_started) * 1000
            retrieval_started = perf_counter()
            outcome = "skipped"
            cache_metrics = {"cache": "bypass_custom_or_skipped"}
            try:
                if decision.retrieve:
                    outcome = "error"
                    search_args = {"module": module_name, "query": _knowledge_query(state), "top_k": config.top_k}
                    if isinstance(context.knowledge_base, DatabaseKnowledgeBase):
                        subject, session = state.get("subject_id"), state.get("session_id")
                        scope = (subject, session, str(bool((state.get("memory") or {}).get("sandbox_mode")))) if subject and session else None
                        knowledge, cache_metrics = await context.knowledge_base.search_with_diagnostics(
                            **search_args, cache_scope=scope)
                    else:
                        knowledge = await context.knowledge_base.search(**search_args)
                    outcome = "returned" if knowledge else "empty"
                    if cache_metrics.get("status", "").startswith("withheld_"):
                        outcome = "error"
            finally:
                retrieval_ms = (perf_counter() - retrieval_started) * 1000 if decision.retrieve else 0.0
                retrieval_metrics = {
                    "gate": decision.as_dict(),
                    "gate_duration_ms": round(gate_ms, 4),
                    "search_duration_ms": round(retrieval_ms, 4),
                    "total_duration_ms": round(gate_ms + retrieval_ms, 4),
                    "outcome": outcome,
                    "returned": len(knowledge),
                    "context_chars": sum(len(chunk.text) for chunk in knowledge),
                    "retriever": type(context.knowledge_base).__name__,
                    "ranking_mode": getattr(context.knowledge_base, "ranking_mode", "custom"),
                    "module": module_name,
                    "exact_cache": cache_metrics,
                }
                # Structured server log: no query, transcript, user ID, or reference text.
                logger.info("retrieval_gate %s", json.dumps(retrieval_metrics, ensure_ascii=False))
            _emit(
                {
                    "type": "trace",
                    "node": module_name,
                    "detail": {
                        "retrieved": [chunk.id for chunk in knowledge],
                        "top_score": knowledge[0].score if knowledge else None,
                        "score_type": knowledge[0].score_type if knowledge else None,
                        "duration_ms": round(gate_ms + retrieval_ms),
                        "intent_gate": retrieval_metrics,
                    },
                }
            )

        global_prompt = None
        module_prompt = None
        mediator_prompt = MEDIATOR_PROMPT
        if context.prompt_snapshot is not None:
            global_prompt = context.prompt_snapshot.get("global")
            module_prompt = context.prompt_snapshot.get(module_name)
            mediator_prompt = context.prompt_snapshot.get("knowledge_mediator", MEDIATOR_PROMPT)
        elif context.sessionmaker is not None:
            try:
                async with context.sessionmaker() as prompt_db:
                    global_prompt, module_prompt = await effective_prompt_pair(
                        prompt_db, module_name
                    )
                    if knowledge and context.settings.knowledge_mediator_enabled:
                        mediator_prompt = await effective_mediator_prompt(prompt_db)
            except Exception:  # noqa: BLE001
                # Prompt administration is an operational convenience, not a
                # reason to make coaching unavailable. A database/table issue
                # falls back to the source-controlled defaults and is logged.
                logger.exception("failed to load prompt overrides for %s", module_name)

        # Keep an immutable turn snapshot before the safety-context fail-closed path.
        recalled_knowledge = list(knowledge)
        context_withheld = False
        mediator_state = dict(state)
        from ..v2_profile import enabled as knowledge_v2_enabled
        if knowledge_v2_enabled() and context.sessionmaker and state.get("subject_id") and state.get("session_id"):
            from ..knowledge_context import assemble_knowledge_context
            try:
                mediator_state["knowledge_context"] = await assemble_knowledge_context(
                    context.sessionmaker, state["subject_id"], state["session_id"])
            except Exception:
                # Unknown safety context must not enable unreviewed retrieval.
                knowledge = []
                context_withheld = True
                logger.warning("knowledge_context unavailable; withholding retrieved knowledge")
        mediator_debug = {}
        guided_knowledge, guidance_block, mediator_metrics = await mediate_knowledge(
            state=mediator_state, module=module_name, knowledge=knowledge, provider=context.router_provider,
            settings=context.settings, prompt=mediator_prompt, debug_output=mediator_debug)
        _emit({"type":"trace", "node":"knowledge_mediator", "detail":mediator_metrics})
        logger.info("knowledge_mediator %s", json.dumps({k:v for k,v in mediator_metrics.items() if k not in ("guidance","cautions")},ensure_ascii=False))
        if context.sessionmaker is not None and mediator_metrics["status"] != "skipped":
            await save_ai_event(context.sessionmaker, stage="knowledge_mediator", session_id=state.get("session_id"),
                subject_id=state.get("subject_id"), provider=context.router_provider.name,
                model_name=mediator_metrics.get("model"), duration_ms=mediator_metrics["duration_ms"],
                usage=mediator_metrics.get("usage"), error_code=mediator_metrics["reason"] if mediator_metrics["status"] == "fallback" else None,
                prompt_version=mediator_metrics.get("prompt_sha256"),
                event_metadata={k:v for k,v in mediator_metrics.items() if k not in ("guidance","cautions","usage")})
        system = build_system_segments(
            module_name,
            metadata=state.get("metadata"),
            knowledge=guided_knowledge,
            memory=state.get("memory"),
            long_term_memory=state.get("long_term_memory"),
            clinical_context=state.get("clinical_context"),
            profile_context=state.get("profile_context"),
            module_steps=state.get("module_steps"),
            global_prompt=global_prompt,
            module_prompt=module_prompt,
        )
        if guidance_block:
            system.append(SystemPromptSegment(guidance_block, cacheable=False))
        authority = await reply_authority()
        if authority is not None:
            system.append(SystemPromptSegment(workflow_prompt(authority), cacheable=False))
        telemetry = dict(state.get("telemetry") or {})
        telemetry["retrieval"] = retrieval_metrics
        telemetry["knowledge_mediator"] = mediator_metrics
        from ..knowledge_references import reference_snapshot
        telemetry["knowledge_references"] = reference_snapshot(
            module=module_name, recalled=recalled_knowledge, provided=guided_knowledge,
            retrieval=retrieval_metrics, mediator=mediator_metrics,
            context_withheld=context_withheld,
            mediator_reasoning=mediator_debug.get("reasoning_content"),
        )
        telemetry["prompt_version"] = hashlib.sha256(
            as_text(system).encode("utf-8")
        ).hexdigest()[:16]
        messages = [*(state.get("chat_history") or []), Message(role="user", content=user_input)]

        update: dict = {
            "retrieved_knowledge": guided_knowledge,
            "provider": context.provider.name,
            "model": context.provider.model,
            "error": None,
        }

        # Declared outside the try so a mid-stream failure can still recover
        # whatever was already produced.
        reply_buffer = VisibleReplyBuffer.create()
        content_guard = ThinkingTagStreamGuard.create()
        reasoning_parts: list[str] = []
        generation_started = perf_counter()
        first_reasoning_seen = False
        first_content_seen = False
        try:
            if context.stream:
                async for delta in context.provider.stream(
                    system=system, messages=messages
                ):
                    elapsed_ms = int((perf_counter() - generation_started) * 1000)
                    if delta.kind == "usage":
                        if delta.usage:
                            update["usage"] = delta.usage
                            telemetry["usage"] = delta.usage
                        if delta.finish_reason:
                            telemetry["finish_reason"] = delta.finish_reason
                        if delta.request_id:
                            telemetry["provider_request_id"] = delta.request_id
                        continue
                    if delta.kind == "reasoning":
                        if not first_reasoning_seen:
                            telemetry["time_to_first_reasoning_token_ms"] = elapsed_ms
                            first_reasoning_seen = True
                        reasoning_parts.append(delta.text)
                        # Hold provider thinking until both channels are
                        # complete. Compatible endpoints sometimes place a
                        # second response in reasoning_content; streaming it
                        # immediately would mislabel that text in the UI.
                        continue
                    if not first_content_seen:
                        telemetry["time_to_first_content_token_ms"] = elapsed_ms
                        first_content_seen = True
                    for guarded_delta in content_guard.push(delta.text):
                        for visible_delta in reply_buffer.push(guarded_delta):
                            emit_output({"type": "delta", "text": visible_delta})
                visible, disclosed_reasoning, final_deltas = _finish_stream_channels(
                    content_guard, reply_buffer, reasoning_parts
                )
                for visible_delta in final_deltas:
                    emit_output({"type": "delta", "text": visible_delta})
                if disclosed_reasoning:
                    emit_output({"type": "reasoning_delta", "text": disclosed_reasoning})
                update["final_response"] = visible
                update["reasoning_content"] = disclosed_reasoning
                update.setdefault("usage", {})
            else:
                completion = await context.provider.complete(
                    system=system, messages=messages
                )
                normalized = normalize_reasoning_channels(
                    unwrap_chat_reply(completion.text), completion.reasoning_content
                )
                update["final_response"] = normalized.reply
                update["reasoning_content"] = normalized.reasoning
                update["model"] = completion.model
                update["usage"] = completion.usage
                telemetry["usage"] = completion.usage
                telemetry["time_to_first_content_token_ms"] = int(
                    (perf_counter() - generation_started) * 1000
                )
                if completion.reasoning_content:
                    telemetry["time_to_first_reasoning_token_ms"] = telemetry[
                        "time_to_first_content_token_ms"
                    ]
                if completion.finish_reason:
                    telemetry["finish_reason"] = completion.finish_reason
                if completion.request_id:
                    telemetry["provider_request_id"] = completion.request_id
        except ProviderError as exc:
            # Do not re-raise: converge on the post-processing node so any
            # partial answer is still persisted and the caller gets a
            # structured error instead of a severed stream.
            logger.warning(
                "%s failed for session %s: %s",
                module_name,
                state.get("session_id"),
                exc,
            )
            visible, disclosed_reasoning, final_deltas = _finish_stream_channels(
                content_guard, reply_buffer, reasoning_parts
            )
            for visible_delta in final_deltas:
                emit_output({"type": "delta", "text": visible_delta})
            if disclosed_reasoning:
                emit_output({"type": "reasoning_delta", "text": disclosed_reasoning})
            update["final_response"] = visible
            update["reasoning_content"] = disclosed_reasoning
            update["error"] = str(exc)
            update["usage"] = {}
            telemetry["error_code"] = "provider_error"

        if context.settings.answer_validator_enabled or authoritative:
            authority = await reply_authority()  # recheck after generation, including other-chat updates
            validation = validate_answer(reply=update.get("final_response", ""), module=module_name,
                evidence_ids=[str(k.id) for k in guided_knowledge], workflow=authority)
            telemetry["answer_validator"] = validation
            _emit({"type": "trace", "node": "answer_validator", "detail": validation})
            block_codes = {f['code'] for f in validation['findings'] if f['severity'] == 'block'}
            if block_codes and block_codes <= {'uncommitted_workflow_claim', 'panel_confirmation_instruction'} and not update.get('error'):
                update['final_response'] = truthful_workflow_reply(authority)
                update['reasoning_content'] = ''
                validation['status'] = 'corrected'
                validation['replacement_source'] = 'database_workflow'
                if context.stream:
                    _emit({'type':'delta','text':update['final_response']})
            elif validation["status"] == "blocked":
                # An upstream failure with no answer keeps its existing error
                # contract; do not invent a successful assistant turn for it.
                update["final_response"] = "" if update.get("error") and not update.get("final_response") else SAFE_REPLY
                update["reasoning_content"] = ""
                update["error"] = update.get("error") or "回复未通过完整性检查"
                if context.stream and update["final_response"]:
                    _emit({"type": "delta", "text": update["final_response"]})
            else:
                for event in pending_output:
                    _emit(event)
        else:
            telemetry["answer_validator"] = {"status": "disabled", "llm_calls": 0}

        telemetry["main_generation_duration_ms"] = int(
            (perf_counter() - generation_started) * 1000
        )
        update["telemetry"] = telemetry

        return update

    module_node.__name__ = f"{module_name}_node"
    module_node.__qualname__ = module_node.__name__
    return module_node


MODULE_NODES = {
    name: make_module_node(name, MODULE_CONFIGS.get(name, ModuleConfig()))
    for name in MODULE_PROMPTS
}


# ---------------------------------------------------------------------------
# Step 3a — the risk gate, and the branch it opens
# ---------------------------------------------------------------------------


async def risk_gate_node(state: AgentState, runtime: Runtime[GraphContext]) -> dict:
    """Screen this turn *before* answering, and divert if it is a crisis.

    This is the one place in the graph that deliberately spends latency. Every
    other check that could be deferred is deferred; this one cannot be, because
    its whole purpose is to decide what the person is about to be told. Running
    it after the fact — which is what the background version did — means the
    normal coaching reply has already been delivered by the time anyone knows
    the turn was a crisis.

    Costs one extra router-model call per turn. `risk_gate_enabled` turns it
    off for anyone who would rather have the latency back.

    Fails **open** on error: if the check itself breaks, the turn proceeds as
    normal coaching rather than being blocked. A crisis reply generated from a
    failed screen would be both wrong and alarming, and the module prompts
    still carry their own risk-handling instructions underneath.
    """
    telemetry = dict(state.get("telemetry") or {})
    if not runtime.context.settings.risk_gate_enabled:
        telemetry["risk_gate_duration_ms"] = 0
        return {"risk": None, "telemetry": telemetry}

    started = perf_counter()
    try:
        raw, completion = await assess_risk_detailed(
            # Safety classification is a short deterministic extraction job,
            # not part of the user's model preference. Keep it on DeepSeek's
            # fast non-thinking router so choosing Doubao does not add a
            # second Ark call before every visible reply.
            runtime.context.router_provider,
            user_message=state["user_input"],
        )
        risk = coerce(RISK_SPECS, raw)
        if runtime.context.sessionmaker is not None:
            # This write is local and tiny; keeping it in the already-blocking
            # safety gate avoids racing the main transcript transaction on
            # single-connection SQLite test/dev databases.
            await save_ai_event(
                    runtime.context.sessionmaker,
                    stage="risk_gate",
                    session_id=state.get("session_id"),
                    subject_id=state.get("subject_id"),
                    provider=runtime.context.router_provider.name,
                    model_name=completion.model,
                    duration_ms=int((perf_counter() - started) * 1000),
                    usage=completion.usage,
                    request_id=completion.request_id,
                    finish_reason=completion.finish_reason,
                    error_code=None if completion.text else "empty_completion",
                    prompt_version=hashlib.sha256(b"risk_gate_v1").hexdigest()[:16],
            )
    except Exception:  # noqa: BLE001 — see docstring: fail open
        logger.exception("risk gate failed; continuing as a normal turn")
        telemetry["risk_gate_duration_ms"] = int((perf_counter() - started) * 1000)
        return {"risk": None, "telemetry": telemetry}

    telemetry["risk_gate_duration_ms"] = int((perf_counter() - started) * 1000)

    flagged = bool(risk.get("risk_status"))
    _emit({"type": "trace", "node": "risk_gate", "detail": {"flagged": flagged}})

    if flagged:
        logger.warning(
            "risk gate DIVERTED session %s (type=%s)",
            state.get("session_id"),
            risk.get("risk_expression_type"),
        )
    return {"risk": risk if flagged else None, "telemetry": telemetry}


def route_after_risk(state: AgentState) -> str:
    """Crisis branch, or the module the intent analysis picked."""
    if state.get("risk"):
        return "crisis"
    return route_after_intent(state)


async def crisis_node(state: AgentState, runtime: Runtime[GraphContext]) -> dict:
    """Answer a flagged turn. Replaces the module entirely for this turn.

    The empathic half is generated — it has to respond to what this person
    actually said — but `CRISIS_RESOURCES` is appended verbatim rather than
    left to the model, because a hallucinated hotline number is worse than no
    number at all. The prompt tells the model not to include one for exactly
    that reason.

    On a provider failure the resources still go out on their own. That is the
    one part of this reply that genuinely must not be lost.
    """
    context = runtime.context
    messages = [
        *(state.get("chat_history") or []),
        Message(role="user", content=state["user_input"]),
    ]
    system = [SystemPromptSegment(CRISIS_PROMPT, cacheable=True)]

    reply_buffer = VisibleReplyBuffer.create()
    content_guard = ThinkingTagStreamGuard.create()
    reasoning_parts: list[str] = []
    update: dict = {
        "provider": context.provider.name,
        "model": context.provider.model,
        "retrieved_knowledge": [],
        "error": None,
        "usage": {},
    }
    telemetry = dict(state.get("telemetry") or {})
    telemetry["prompt_version"] = hashlib.sha256(
        as_text(system).encode("utf-8")
    ).hexdigest()[:16]
    generation_started = perf_counter()
    first_reasoning_seen = False
    first_content_seen = False

    try:
        if context.stream:
            async for delta in context.provider.stream(system=system, messages=messages):
                elapsed_ms = int((perf_counter() - generation_started) * 1000)
                if delta.kind == "usage":
                    if delta.usage:
                        update["usage"] = delta.usage
                        telemetry["usage"] = delta.usage
                    if delta.finish_reason:
                        telemetry["finish_reason"] = delta.finish_reason
                    if delta.request_id:
                        telemetry["provider_request_id"] = delta.request_id
                    continue
                if delta.kind == "reasoning":
                    if not first_reasoning_seen:
                        telemetry["time_to_first_reasoning_token_ms"] = elapsed_ms
                        first_reasoning_seen = True
                    reasoning_parts.append(delta.text)
                    continue
                if not first_content_seen:
                    telemetry["time_to_first_content_token_ms"] = elapsed_ms
                    first_content_seen = True
                for guarded_delta in content_guard.push(delta.text):
                    for visible_delta in reply_buffer.push(guarded_delta):
                        _emit({"type": "delta", "text": visible_delta})
        else:
            completion = await context.provider.complete(system=system, messages=messages)
            normalized = normalize_reasoning_channels(
                unwrap_chat_reply(completion.text), completion.reasoning_content
            )
            reply_buffer.raw_parts.append(normalized.reply)
            reasoning_parts.append(normalized.reasoning)
            update["model"] = completion.model
            update["usage"] = completion.usage
            telemetry["usage"] = completion.usage
            telemetry["time_to_first_content_token_ms"] = int(
                (perf_counter() - generation_started) * 1000
            )
            if completion.reasoning_content:
                telemetry["time_to_first_reasoning_token_ms"] = telemetry[
                    "time_to_first_content_token_ms"
                ]
            if completion.finish_reason:
                telemetry["finish_reason"] = completion.finish_reason
            if completion.request_id:
                telemetry["provider_request_id"] = completion.request_id
    except ProviderError as exc:
        logger.warning("crisis node failed for session %s: %s", state.get("session_id"), exc)
        telemetry["error_code"] = "provider_error"

    if context.stream:
        visible, disclosed_reasoning, final_deltas = _finish_stream_channels(
            content_guard, reply_buffer, reasoning_parts
        )
        for visible_delta in final_deltas:
            _emit({"type": "delta", "text": visible_delta})
        if disclosed_reasoning:
            _emit({"type": "reasoning_delta", "text": disclosed_reasoning})
        _emit({"type": "delta", "text": CRISIS_RESOURCES})
    else:
        visible = unwrap_chat_reply("".join(reply_buffer.raw_parts))
        disclosed_reasoning = "".join(reasoning_parts)

    update["final_response"] = visible + CRISIS_RESOURCES
    update["reasoning_content"] = disclosed_reasoning
    telemetry["main_generation_duration_ms"] = int(
        (perf_counter() - generation_started) * 1000
    )
    update["telemetry"] = telemetry
    return update


# ---------------------------------------------------------------------------
# Step 4 — post-hoc routing
# ---------------------------------------------------------------------------


async def route_next_module_node(
    state: AgentState, runtime: Runtime[GraphContext]
) -> dict:
    """Resolve rule-only cases and mark ordinary routing for background work.

    The Router Agent used to be awaited here, which kept the HTTP response and
    input lock open after the visible answer had already finished. Ordinary
    successful turns now hold their current module just long enough to persist
    the reply; ``schedule_background_routing`` performs the model call and
    atomically publishes the real next-module decision afterwards.
    """
    current = state.get("extracted_intent", DEFAULT_MODULE)

    if state.get("error"):
        # Nothing happened this turn worth reasoning about — hold position
        # rather than spend a second model call on a turn that already failed.
        return {
            "next_module": current,
            "routing_reasoning_content": "模块判断结果：维持当前模块。\n\n本轮回复生成失败，因此没有调用模块路由模型。",
            "router_model_name": "",
            "routing_pending": False,
        }

    if state.get("risk"):
        # A crisis turn never advances the programme. The module's own exit
        # conditions were not met — it did not even run — and asking the router
        # to reason about "progress" from a crisis exchange would let a
        # distressed turn push someone into goal-setting.
        explanation = (
            "模块判断结果：维持当前模块。\n\n"
            "本轮触发危机应答，正常模块流程被中断；按照安全规则，危机轮次不会推进模块。"
        )
        _emit({"type": "routing_reasoning", "text": explanation, "model": "规则引擎"})
        return {
            "next_module": current,
            "routing_reasoning_content": explanation,
            "router_model_name": "规则引擎",
            "routing_pending": False,
        }
    return {
        "next_module": current,
        "routing_reasoning_content": "",
        "router_model_name": "",
        "routing_pending": True,
    }


# ---------------------------------------------------------------------------
# Step 4b — background summarization
# ---------------------------------------------------------------------------

SUMMARIZER_PROMPT = """\
你是 BA Coach 的记忆总结助手。任务：把用户在某一模块内的对话，压缩成一段供未来对话回忆使用的高信息密度摘要。

# 要求
- 只输出摘要正文本身，不要输出任何解释、前后缀、标题或 markdown 标记
- 中文，300-450 字左右，信息密度优先于流畅度
- 依据实际发生的模块，重点记录：
  - 模块一：用户的核心困扰/主诉、具体触发事件、已确认的抑郁循环
  - 模块二：确立的 PA 目标卡片——活动内容、时间、地点、时长、潜在障碍与应对方案
  - 模块三：达成的记录契约与记录方式
  - 模块四：本次执行结果、ABC 分析要点、识别出的行为模式、下一步策略
- 只保留对未来对话仍然有用的事实性信息，省略寒暄、重复确认等过程性文字
- 严禁编造对话中没有出现的信息
"""

# Strong references to in-flight background tasks — asyncio only holds a weak
# reference internally, so a task with nothing else pointing at it can be
# garbage-collected mid-flight (a well-known asyncio footgun). Every task this
# module spawns lives here from creation until its own done-callback removes
# it, which is what lets it actually outlive the request handler that started
# it.
_background_tasks: set[asyncio.Task] = set()


def _spawn_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _summarize_and_save(
    provider: LLMProvider,
    memos: MemosIntegrationManager,
    *,
    subject_id: str,
    settings_summarizer_max_tokens: int,
    transcript: str,
    from_module: str,
    to_module: str,
    sessionmaker=None,
    session_id: str | None = None,
) -> None:
    """The actual summarize-then-save work, run detached from the request.

    Never raises — this runs after the response has already gone out, so
    there is no request left to fail; a broken summarizer should be a log
    line, not a crashed background task.
    """
    try:
        started = perf_counter()
        completion = await provider.route_detailed(
            system=SUMMARIZER_PROMPT,
            user=f"（本轮从 {from_module} 切换到 {to_module}）\n\n{transcript}",
            max_tokens=settings_summarizer_max_tokens,
        )
        summary = completion.text
        if sessionmaker is not None:
            await save_ai_event(
                sessionmaker,
                stage="module_summarizer",
                session_id=session_id,
                subject_id=subject_id,
                provider=provider.name,
                model_name=completion.model,
                duration_ms=int((perf_counter() - started) * 1000),
                usage=completion.usage,
                request_id=completion.request_id,
                finish_reason=completion.finish_reason,
                error_code=None if completion.text else "empty_completion",
                prompt_version=hashlib.sha256(
                    SUMMARIZER_PROMPT.encode("utf-8")
                ).hexdigest()[:16],
            )
        if not summary.strip():
            logger.warning("summarizer produced nothing for subject %s", subject_id[:8])
            return
        from ..v2_profile import enabled as v2_enabled
        if v2_enabled() and sessionmaker is not None:
            from ..v2_repository import append_memory
            async with sessionmaker() as db:
                from ..models import Conversation, ConversationMessage
                from sqlalchemy import select
                # A slow summary must not recreate memory after source deletion.
                source_conversation = (await db.execute(select(Conversation.id).where(
                    Conversation.session_id == session_id,
                    Conversation.subject_id == subject_id).with_for_update())).scalar_one_or_none()
                if source_conversation is None:
                    return
                source_message = (await db.execute(select(ConversationMessage.id).where(
                    ConversationMessage.conversation_id == source_conversation,
                    ConversationMessage.role == "user").order_by(ConversationMessage.position.desc()).limit(1))).scalar_one_or_none()
                if source_message is None:
                    return
                await append_memory(db, user_id=subject_id, memory_type="模块总结",
                                    content=summary.strip(), source_kind="ai_inference",
                                    source_message_id=source_message)
                await db.commit()
        elif memos is not None:
            await memos.save_memo(subject_id, summary.strip())
    except Exception:  # noqa: BLE001 — background task, nothing to propagate to
        logger.exception("background summarization failed for subject %s", subject_id[:8])


async def _extract_module_data(
    provider: LLMProvider,
    sessionmaker,
    *,
    subject_id: str,
    module: str,
    transcript: str,
    max_tokens: int,
    session_id: str | None = None,
    evidence_turns: list[tuple[str, str]] | None = None,
) -> dict:
    """Extract and audit validated module data without writing a record."""
    started = perf_counter()
    raw, completion = await extract_module_record_detailed(
        provider, module=module, transcript=transcript, max_tokens=max_tokens
    )
    data = coerce(MODULE_SPECS[module], raw)
    if module == "module_4" and isinstance(raw.get("m4_contract"), dict):
        data["m4_contract"] = raw["m4_contract"]
    if module in {"module_2", "module_4"}:
        for key in ("goal_proposal", "plan_context", "activity_observations", "review_followup", "activity_corrections"):
            if key in raw and isinstance(raw[key], (dict, list)):
                data[key] = raw[key]
    if module == "module_1" and evidence_turns is not None:
        from ..m1_contract import snapshot
        data = snapshot(raw, data, evidence_turns, session_id)
    await save_ai_event(
        sessionmaker,
        stage="clinical_extraction",
        session_id=session_id,
        subject_id=subject_id,
        provider=provider.name,
        model_name=completion.model,
        duration_ms=int((perf_counter() - started) * 1000),
        usage=completion.usage,
        request_id=completion.request_id,
        finish_reason=completion.finish_reason,
        error_code=None if completion.text else "empty_completion",
        prompt_version=hashlib.sha256(
            ("clinical_" + module + ("_20260914" if module == "module_1" else "_20260917" if module == "module_4" else "_v1")).encode("utf-8")
        ).hexdigest()[:16],
        event_metadata={"module": module, **({"contract_version": data["m1_contract"]["version"],
            "contract_path": data["m1_contract"]["path"], "missing_fields": data["m1_contract"]["missing_fields"]}
            if "m1_contract" in data else {})},
    )
    return data


async def _extract_and_persist_module(
    provider: LLMProvider,
    sessionmaker,
    *,
    subject_id: str,
    module: str,
    transcript: str,
    max_tokens: int,
    reuse_latest: bool,
    cycle_id: str | None = None,
    session_id: str | None = None,
    evidence_turns: list[tuple[str, str]] | None = None,
    evidence_turn_id: int | None = None,
) -> None:
    """Extract one module's fields and write them. Never raises."""
    try:
        data = await _extract_module_data(provider, sessionmaker,
            subject_id=subject_id, module=module, transcript=transcript,
            max_tokens=max_tokens, session_id=session_id, evidence_turns=evidence_turns)
        # Do not turn an empty/invalid extraction into a nominally successful
        # source write by adding bookkeeping keys first.  In V2 that would
        # otherwise make a stale draft look fresh after a correction.
        if not data:
            logger.info("clinical: nothing extractable for %s (%s…)", module, subject_id[:8])
            return
        if "m1_contract" in data:
            data["m1_contract"]["assistant_message_id"] = evidence_turn_id
        if module in {"module_2", "module_3", "module_4"}:
            data["_source_session_id"] = session_id
            data["_source_assistant_message_id"] = evidence_turn_id
        await persist_module_record(
            sessionmaker,
            module=module,
            user_id=subject_id,
            data=data,
            reuse_latest=reuse_latest,
            cycle_id=cycle_id,
        )
    except Exception:  # noqa: BLE001 — background task, nothing to propagate to
        logger.exception("clinical extraction failed for %s (%s…)", module, subject_id[:8])


async def _persist_risk_quietly(sessionmaker, *, subject_id: str, data: dict) -> None:
    """Write the risk row the gate already decided on. Never raises.

    Only the *write* is backgrounded. The judgement itself happened in
    `risk_gate_node`, before the reply, because it changes what the person is
    told; recording it does not, so it does not need to hold up the response.
    """
    try:
        await persist_risk(sessionmaker, user_id=subject_id, data=data)
    except Exception:  # noqa: BLE001 — background task, nothing to propagate to
        logger.exception("persisting risk failed for %s…", subject_id[:8])


def _format_transcript(history: list[Message], user_input: str, reply: str) -> str:
    lines = [f"{m.role}：{m.content}" for m in history if m.content]
    lines.append(f"user：{user_input}")
    if reply:
        lines.append(f"assistant：{reply}")
    return "\n".join(lines)


def _format_router_transcript(
    history: list[Message], user_input: str, reply: str
) -> str:
    """Keep both the beginning and end of a long module conversation.

    Module-one evidence is naturally distributed: the concrete situation is
    often near the beginning, while understanding/consent appears near the
    end. Keeping only the latest messages makes the router repeatedly forget
    the first half of its own exit criteria; sending an unbounded transcript
    would make a cheap routing call grow forever.
    """
    transcript = _format_transcript(history, user_input, reply)
    if len(transcript) <= ROUTER_TRANSCRIPT_CHARS:
        return transcript
    head_chars = ROUTER_TRANSCRIPT_CHARS // 3
    tail_chars = ROUTER_TRANSCRIPT_CHARS - head_chars
    return (
        transcript[:head_chars]
        + "\n\n[中间较早的重复对话已省略]\n\n"
        + transcript[-tail_chars:]
    )


def _dispatch_transition_jobs(
    state: AgentState,
    context: GraphContext,
    *,
    current: str,
    target: str,
    cycle_id: str | None = None,
) -> None:
    """Launch extraction/profile/Memos work after a confirmed transition."""
    subject_id = state.get("subject_id")
    if (
        not subject_id
        or current == target
        or state.get("error")
        or (state.get("memory") or {}).get("sandbox_mode") == "true"
    ):
        return

    transcript = _format_transcript(
        state.get("chat_history") or [],
        state["user_input"],
        state.get("final_response", ""),
    )
    if context.sessionmaker is not None and not (context.settings.database_schema_version == "v2" and current in {"module_1", "module_2", "module_3", "module_4"}):
        async def _clinical_jobs() -> None:
            # Keep these two writes sequential. Apart from avoiding needless
            # pool pressure in production, test SQLite uses one shared
            # connection and concurrent transactions can roll each other back.
            await _extract_and_persist_module(
                context.provider,
                context.sessionmaker,
                subject_id=subject_id,
                module=current,
                transcript=transcript,
                max_tokens=context.settings.extraction_max_tokens,
                reuse_latest=(state.get("memory") or {}).get("last_module") == current,
                cycle_id=cycle_id,
                session_id=state.get("session_id"),
            )

        _spawn_background(_clinical_jobs())
    from ..v2_profile import enabled as v2_enabled
    if context.memos is not None or v2_enabled():
        _spawn_background(
            _summarize_and_save(
                context.provider,
                context.memos,
                subject_id=subject_id,
                settings_summarizer_max_tokens=context.settings.summarizer_max_tokens,
                transcript=transcript,
                from_module=current,
                to_module=target,
                sessionmaker=context.sessionmaker,
                session_id=state.get("session_id"),
            )
        )


# One pending route per session. The strong task reference prevents garbage
# collection; the keyed map also lets a very fast next user turn wait for the
# preceding decision instead of entering the old module by racing it.
_routing_tasks: dict[str, asyncio.Task] = {}


async def wait_for_pending_routing(session_id: str | None) -> None:
    if not session_id:
        return
    task = _routing_tasks.get(session_id)
    if task is not None:
        await asyncio.shield(task)


async def _run_background_routing(
    state: AgentState,
    context: GraphContext,
    *,
    assistant_message_id: int | None,
) -> None:
    session_id = state["session_id"]
    current = state.get("extracted_intent", DEFAULT_MODULE)
    started = perf_counter()
    try:
        reply = state.get("final_response", "")
        m1_turns = None
        if (current in {"module_1", "module_4"} and context.settings.database_schema_version == "v2"
                and context.sessionmaker is not None and state.get("subject_id")):
            # M1 evidence can span more than the short-term 40-message window.
            # Read only this user's persisted conversation, with trusted roles.
            from sqlalchemy import select
            from ..models import Conversation, ConversationMessage
            async with context.sessionmaker() as evidence_db:
                rows = (await evidence_db.execute(select(ConversationMessage).join(
                    Conversation, Conversation.id == ConversationMessage.conversation_id).where(
                    Conversation.session_id == session_id, Conversation.subject_id == state["subject_id"])
                    .order_by(ConversationMessage.position))).scalars().all()
                if rows and rows[-1].id == assistant_message_id:
                    if current == "module_4":
                        from ..m4_contract import cycle_messages
                        rows = await cycle_messages(evidence_db, rows)
                    m1_turns = [(m.role, m.content) for m in rows if m.content]
            if m1_turns is None:
                m1_turns = [(m.role, m.content) for m in state.get('chat_history') or [] if m.content]
                m1_turns += [("user", state['user_input']), ("assistant", reply)]
        if (context.settings.database_schema_version == "v2" and current in {"module_1", "module_2", "module_3", "module_4"}
                and context.sessionmaker is not None and state.get("subject_id")
                and (current == "module_1" or state.get("active_cycle_id"))
                and not state.get("error")
                and (state.get("memory") or {}).get("sandbox_mode") != "true"):
            # A draft is a prerequisite for confirmation, not a consequence of
            # a module transition. Extract before judging readiness each turn.
            await _extract_and_persist_module(context.provider, context.sessionmaker,
                subject_id=state['subject_id'], module=current,
                transcript=(("当前系统目标背景（不是用户执行事实）：\n" + "\n".join(state.get("clinical_context") or []) + "\n本周期对话：\n") if current == "module_4" else "") + ("\n".join(f"{role}：{content}" for role, content in m1_turns) if m1_turns is not None else _format_transcript(state.get('chat_history') or [],state['user_input'],reply)),
                max_tokens=context.settings.extraction_max_tokens,reuse_latest=True,
                cycle_id=state.get('active_cycle_id'),session_id=session_id,
                evidence_turns=m1_turns,
                evidence_turn_id=assistant_message_id)
            from ..v2_workflow import load_workflow
            fresh_steps, _ = await load_workflow(context.sessionmaker,session_id)
            state = {**state, 'module_steps':fresh_steps}
        has_pa_card = bool(
            (state.get("memory") or {}).get("pa_card") or extract_pa_card(reply)
        )
        if context.settings.database_schema_version == "v2" and current == "module_2":
            # The draft/checklist and confirmation service own plan validity.
            # Do not require a literal card heading in this turn's prose.
            has_pa_card = bool(state.get('active_cycle_id'))
        decision = await decide_target_module_with_reasoning(
            context.router_provider,
            current_module=current,
            user_input=state["user_input"],
            ai_output=reply,
            has_pa_card=has_pa_card,
            conversation_context=_format_router_transcript(
                state.get("chat_history") or [], state["user_input"], reply
            ),
            system_prompt=context.router_prompt,
            max_tokens=context.settings.router_reasoning_max_tokens,
            completed_steps=(state.get("module_steps") or {}).get(current, []),
        )
        new_goal_data = None
        if (context.settings.database_schema_version == "v2" and current == "module_2"
                and context.sessionmaker is not None and state.get("subject_id")
                and not state.get("active_cycle_id")
                and not state.get("error") and not (state.get("memory") or {}).get("sandbox_mode")):
            try:
                new_goal_data = await _extract_module_data(
                    context.provider, context.sessionmaker,
                    subject_id=state["subject_id"], module=current,
                    transcript=_format_transcript(
                        state.get("chat_history") or [], state["user_input"], reply),
                    max_tokens=context.settings.extraction_max_tokens,
                    session_id=session_id,
                )
            except Exception:  # extraction failure must never invent a goal
                logger.exception("new-goal extraction failed for session %s", session_id)
        target = decision.target_module
        requested_target = target
        result_line = (
            f"模块判断结果：维持 {current}，本轮不跳转。"
            if target == current
            else f"模块判断结果：{current} → {target}。"
        )
        thought = decision.reasoning_content.strip() or (
            "路由模型没有返回独立的 reasoning_content；最终模块判断仍已通过规则校验。"
        )
        routing_reasoning = f"{result_line}\n\n{thought}"
        duration_ms = int((perf_counter() - started) * 1000)
        source_cycle_id = state.get("active_cycle_id")
        active_cycle_id = source_cycle_id

        # Publish to the live session and durable transcript as one logical
        # update. The short lock protects the module pointer only; the slow LLM
        # call above never holds the user's turn lock.
        turn_lock = await context.store.get_turn_lock(session_id)
        async with turn_lock:
            if (
                state.get("subject_id")
                and assistant_message_id is not None
                and context.sessionmaker is not None
            ):
                async with context.sessionmaker() as route_db:
                    from ..v2_profile import enabled as v2_enabled
                    if v2_enabled():
                        from ..v2_workflow import create_goal_from_agent_dialogue, record_steps, runtime_for
                        if new_goal_data:
                            new_goal_data["_source_assistant_message_id"] = assistant_message_id
                            from ..goal_contract import evidence_messages, capture_activities
                            conv, runtime_state = await runtime_for(route_db, session_id)
                            messages = await evidence_messages(route_db, conv.id, state["subject_id"]) if conv else []
                            if runtime_state and messages and messages[-1].id == assistant_message_id:
                                await capture_activities(route_db, user_id=state["subject_id"], conversation=conv,
                                    state=runtime_state, raw=new_goal_data.get("activity_observations"), messages=messages,
                                    corrections=new_goal_data.get("activity_corrections"))
                            created = await create_goal_from_agent_dialogue(
                                route_db, session_id=session_id, user_id=state["subject_id"],
                                data=new_goal_data, completed_steps=decision.completed_steps,
                                assistant_message_id=assistant_message_id)
                            if created:
                                active_cycle_id = created["cycle_id"]
                        target, active_cycle_id = await record_steps(route_db, session_id=session_id,
                            user_id=state["subject_id"], module=current, requested_target=target,
                            steps=decision.completed_steps, assistant_message_id=assistant_message_id,
                            revoked_steps=decision.revoked_steps, revocation_evidence=decision.revocation_evidence)
                        if target != requested_target:
                            routing_reasoning = f"后台核验后的实际阶段：{target}。讨论及确认均在聊天中完成；没有完整、当前有效的用户同意证据时不推进。"
                    else:
                        active_cycle_id = await apply_router_decision(
                            route_db, session_id=session_id, subject_id=state["subject_id"],
                            current_module=current, target_module=target, completed_steps=decision.completed_steps)
                    await record_interaction_transition(
                        route_db,
                        subject_id=state["subject_id"],
                        current_module=current,
                        target_module=target,
                    )
                    # V2 extraction can have just written a durable freshness
                    # marker.  Publish that current database memory, not the
                    # pre-extraction graph snapshot, or this bookkeeping
                    # update would erase the marker before confirmation sees it.
                    published_memory = state.get("memory") or {}
                    if v2_enabled():
                        _, durable_state = await runtime_for(route_db, session_id)
                        if durable_state:
                            published_memory = durable_state["memory"] or {}
                    await complete_background_routing(
                        route_db,
                        subject_id=state["subject_id"],
                        session_id=session_id,
                        assistant_message_id=assistant_message_id,
                        module=target,
                        memory=published_memory,
                        routing_reasoning_content=routing_reasoning,
                        router_model_name=decision.model,
                        router_duration_ms=duration_ms,
                        router_provider=context.router_provider.name,
                        workflow_decision={
                            "schema_version": 1, "registry_version": WORKFLOW_CONTRACT_VERSION,
                            "from_module": current, "to_module": target,
                            "completed_steps": decision.completed_steps,
                            "decision_message_id": assistant_message_id,
                            "evidence_status": "router_inferred_not_individually_verified",
                            "source_cycle_id": source_cycle_id, "active_cycle_id": active_cycle_id,
                        },
                        usage=decision.usage,
                        request_id=decision.request_id,
                        finish_reason=decision.finish_reason,
                        error_code=decision.error_code,
                        prompt_version=hashlib.sha256(
                            (context.router_prompt or "").encode("utf-8")
                        ).hexdigest()[:16],
                    )

            # Publish in-memory state only after the durable transaction passes.
            if context.settings.database_schema_version == "v2" and context.sessionmaker is not None and state.get("subject_id") and assistant_message_id is not None:
                await context.store.set_memory(session_id, published_memory)
            await context.store.set_module(session_id, target)

        _dispatch_transition_jobs(
            state,
            context,
            current=current,
            target=target,
            cycle_id=source_cycle_id or active_cycle_id,
        )
        logger.info(
            "background router session=%s from=%s to=%s duration_ms=%d",
            session_id,
            current,
            target,
            duration_ms,
        )
    except Exception:  # noqa: BLE001 — detached task, response already finished
        logger.exception("background router failed for session %s", session_id)


def schedule_background_routing(
    state: AgentState,
    context: GraphContext,
    *,
    assistant_message_id: int | None,
) -> None:
    """Start the delayed Router Agent after the visible reply is durable."""
    if not state.get("routing_pending"):
        return
    session_id = state["session_id"]
    previous = _routing_tasks.get(session_id)
    if previous is not None and not previous.done():
        # A next request normally awaits this task before reaching here. Keep
        # this guard for direct graph/route callers and avoid replacing a live
        # decision with a second one.
        return
    task = _spawn_background(
        _run_background_routing(
            dict(state), context, assistant_message_id=assistant_message_id
        )
    )
    _routing_tasks[session_id] = task

    def _remove(completed: asyncio.Task) -> None:
        if _routing_tasks.get(session_id) is completed:
            _routing_tasks.pop(session_id, None)

    task.add_done_callback(_remove)


async def summarizer_node(state: AgentState, runtime: Runtime[GraphContext]) -> dict:
    """Dispatch every after-the-turn job — none of them awaited here.

    Three separate pieces of bookkeeping, all detached via `_spawn_background`
    so the graph (and the response already streamed to the client) never waits
    on them:

    * **Risk screening**, every turn. A risk signal does not wait for a module
      boundary, which is why this one is not gated on a transition.
    * **Clinical extraction**, on a module change. That is the point at which
      the module's own record is as complete as it is going to get, and doing
      it per-turn would mean re-extracting and rewriting the same row on every
      exchange for the cost of an extra LLM call each time.
    * **Memos summary**, on a module change — the long-term memory write.

    A router agent could technically produce the summary too, but that
    conflates two different judgement calls (which module comes next vs. what
    is worth remembering) in one prompt; keeping them apart is why this is its
    own node with its own prompt rather than an addition to `router_agent.py`.
    """
    current = state.get("extracted_intent", DEFAULT_MODULE)
    target = state.get("next_module", current)
    subject_id = state.get("subject_id")
    sessionmaker = runtime.context.sessionmaker
    memos = runtime.context.memos
    provider = runtime.context.provider
    changed = target != current

    # Nothing below can be attributed without a subject, and a failed turn has
    # no reliable content to extract from.
    if not subject_id or state.get("error"):
        return {}

    # Administrator sandbox turns are deliberately isolated from the real BA
    # programme record.  The model and router still run normally so prompts
    # and transitions can be tested, but risk rows, module extraction, Memos,
    # and `user_profile.current_module` must not be polluted by test dialogue.
    if (state.get("memory") or {}).get("sandbox_mode") == "true":
        _emit(
            {
                "type": "trace",
                "node": "summarizer",
                "detail": {"sandbox": True, "clinical_writes": False},
            }
        )
        return {}

    # The risk verdict is already decided — risk_gate_node ran before the
    # reply. Persist that, rather than screening the same message a second
    # time: two calls would double the cost and could disagree with each
    # other, and the row must record what actually gated the turn.
    risk = state.get("risk")
    if sessionmaker is not None and risk:
        _spawn_background(
            _persist_risk_quietly(sessionmaker, subject_id=subject_id, data=risk)
        )

    if not changed:
        _emit({"type": "trace", "node": "summarizer", "detail": {"risk_only": True}})
        return {}

    transcript = _format_transcript(
        state.get("chat_history") or [], state["user_input"], state.get("final_response", "")
    )

    if sessionmaker is not None:
        # `reuse_latest`: the module being left is the one whose row this turn
        # has been filling in, so refine that row rather than opening another.
        # A *new* row is only right when the subject comes back to this module
        # on a later cycle, which is the branch `update_profile_module` below
        # marks by moving `current_module` away from it.
        _spawn_background(
            _extract_and_persist_module(
                provider,
                sessionmaker,
                subject_id=subject_id,
                module=current,
                transcript=transcript,
                max_tokens=runtime.context.settings.extraction_max_tokens,
                reuse_latest=(state.get("memory") or {}).get("last_module") == current,
                cycle_id=state.get("active_cycle_id"),
                session_id=state.get("session_id"),
            )
        )

    if memos is not None:
        _spawn_background(
            _summarize_and_save(
                provider,
                memos,
                subject_id=subject_id,
                settings_summarizer_max_tokens=runtime.context.settings.summarizer_max_tokens,
                transcript=transcript,
                from_module=current,
                to_module=target,
                sessionmaker=sessionmaker,
                session_id=state.get("session_id"),
            )
        )

    _emit(
        {
            "type": "trace",
            "node": "summarizer",
            "detail": {"from": current, "to": target, "dispatched": True},
        }
    )
    return {}


# ---------------------------------------------------------------------------
# Step 5 — post-processing / convergence
# ---------------------------------------------------------------------------


def _derive_memory(state: AgentState) -> dict[str, str]:
    """Update durable memory from the finished turn.

    Deliberately cheap and deterministic: turn counter, last module, and a
    short excerpt of what the user said. An LLM-written running summary is the
    obvious upgrade — see the TODO below — but it costs a second model call
    per turn, so it is opt-in rather than default.
    """
    memory = dict(state.get("memory") or {})
    memory["turn_count"] = str(int(memory.get("turn_count", "0")) + 1)
    memory["last_module"] = state.get("extracted_intent", DEFAULT_MODULE)

    # Keep the most recently generated card, not the first — a later cycle
    # through module 2 replaces the goal, and route_next_module_node's
    # PA-card gate needs to see the current one, not a stale earlier one.
    card = extract_pa_card(state.get("final_response", ""))
    if card:
        memory["pa_card"] = card

    excerpt = state["user_input"].strip().replace("\n", " ")
    if len(excerpt) > MEMORY_EXCERPT_CHARS:
        excerpt = excerpt[: MEMORY_EXCERPT_CHARS - 1] + "…"
    memory["last_user_message"] = excerpt

    # TODO: for a rolling narrative summary, call
    # `context.provider.complete(...)` here with a summarisation prompt and
    # store the result under "summary". Budget for the extra call per turn.
    return memory


async def update_memory_and_format_node(
    state: AgentState, runtime: Runtime[GraphContext]
) -> dict:
    """Convergence node: persist the turn, update memory, publish the result.

    All four branches feed into this one node, so persistence and memory
    handling exist exactly once.

    On "formats the final SSE stream": this emits a structured `done` (or
    `error`) event onto the graph's custom stream. Serialising those events
    into `text/event-stream` frames stays in routes/chat.py — the graph should
    not know what transport is carrying it.
    """
    context = runtime.context
    session_id = state["session_id"]
    reply = state.get("final_response", "")
    error = state.get("error")

    # Once validated generation converges, finish atomically instead of letting
    # a late stop leave half-updated in-memory state or interrupt persistence.
    if context.generation is not None:
        context.generation.seal()

    await context.store.append(session_id, Message(role="user", content=state["user_input"]))
    if reply:
        await context.store.append(session_id, Message(role="assistant", content=reply))

    memory = _derive_memory(state)
    await context.store.set_memory(session_id, memory)
    # next_module (route_next_module_node's decision), not extracted_intent
    # (this turn's own module) — that is what analyze_intent_node reads back
    # as current_module on the turn after this one.
    await context.store.set_module(
        session_id, state.get("next_module", state.get("extracted_intent", DEFAULT_MODULE))
    )

    if error:
        _emit({"type": "error", "node": "update_memory_and_format", "detail": error})
    else:
        _emit(
            {
                "type": "done",
                "node": "update_memory_and_format",
                "session_id": session_id,
                "reply_module": state.get("extracted_intent"),
                "next_module": (
                    None
                    if state.get("routing_pending")
                    else state.get("next_module")
                ),
                "routing_pending": bool(state.get("routing_pending")),
                "routed_by": state.get("routed_by"),
                "usage": state.get("usage", {}),
            }
        )

    logger.info(
        "session=%s module=%s via=%s chars=%d error=%s risk_ms=%s "
        "ttfr_ms=%s ttfc_ms=%s main_ms=%s",
        session_id,
        state.get("extracted_intent"),
        state.get("routed_by"),
        len(reply),
        bool(error),
        (state.get("telemetry") or {}).get("risk_gate_duration_ms"),
        (state.get("telemetry") or {}).get("time_to_first_reasoning_token_ms"),
        (state.get("telemetry") or {}).get("time_to_first_content_token_ms"),
        (state.get("telemetry") or {}).get("main_generation_duration_ms"),
    )

    return {"memory": memory, "final_response": reply}
