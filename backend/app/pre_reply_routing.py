"""One module proposal before generation; only committed state selects a reply.

The decision model sees prior dialogue and the current user message. The
confirmation service owns all business mutations and can veto its proposal.
"""
from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from langgraph.config import get_stream_writer
from .router_agent import RouterDecision, decide_target_module_with_reasoning, format_routing_reasoning
from .schemas import Message
from .trace_timing import record_span, prompt_source

logger = logging.getLogger(__name__)
MODULES = {"module_1", "module_2", "module_3", "module_4"}


async def load_routing_snapshot(state, context):
    """Read an owned, compact snapshot; this function never writes state."""
    current = state.get("current_module") or state.get("extracted_intent") or "module_1"
    memory = state.get("memory") or {}
    snapshot = {"current_module": current, "memory": memory,
                "active_cycle_id": state.get("active_cycle_id")}
    compact = {"current_module": current, "has_pa_card": bool(memory.get("pa_card"))}
    if (context.settings.database_schema_version != "v2" or context.sessionmaker is None
            or not state.get("subject_id")):
        return {**snapshot, "routing_state": compact}
    from sqlalchemy import select
    from .database_v2_schema import metadata as schema
    from .models import ConversationMessage
    from .v2_workflow import runtime_for
    async with context.sessionmaker() as db:
        conversation, persisted = await runtime_for(db, state["session_id"])
        if not conversation or conversation.subject_id != state["subject_id"] or not persisted:
            raise ValueError("owned_routing_state_unavailable")
        snapshot.update(current_module=persisted["current_module"],
                        memory=persisted["memory"] or {}, active_cycle_id=persisted["active_cycle_id"])
        compact = {key: persisted[key] for key in ("current_module", "flow_status", "active_goal_id", "active_cycle_id", "row_version")}
        plans, goals, cycles = (schema.tables[name] for name in ("module_two_record", "pa_goals", "pa_cycles"))
        compact["has_pa_card"] = bool(await db.scalar(select(plans.c.id).join(goals,
            goals.c.current_plan_record_id == plans.c.id).join(cycles,
            (cycles.c.module_two_record_id == plans.c.id) & (cycles.c.goal_id == goals.c.id)).where(
            goals.c.id == persisted["active_goal_id"], goals.c.user_id == state["subject_id"],
            goals.c.status == "active", cycles.c.id == persisted["active_cycle_id"],
            cycles.c.status.in_(["planning", "waiting_execution", "reviewing"]),
            plans.c.goal_id == goals.c.id, plans.c.record_status == "confirmed",
            plans.c.confirmation_status == "confirmed")))
        m1 = schema.tables["user_module_one_state"]
        compact["m1_status"] = await db.scalar(select(m1.c.status).where(m1.c.user_id == state["subject_id"]))
        if persisted["current_module"] == "module_3":
            from .knowledge_context import read_recording_state
            recording = await read_recording_state(db, state["subject_id"], persisted)
            snapshot.update(recording)
            compact.update(recording)
        query = select(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id)
        if state.get("user_message_id") is not None:
            boundary = (await db.execute(query.where(ConversationMessage.id == state["user_message_id"],
                ConversationMessage.role == "user"))).scalar_one_or_none()
            if boundary is None:
                raise ValueError("current_user_message_not_owned")
            query = query.where(ConversationMessage.position < boundary.position)
        prior = (await db.execute(query.order_by(ConversationMessage.position, ConversationMessage.id))).scalars().all()
        snapshot["routing_history"] = [Message(role=message.role, content=message.content) for message in prior
                                       if message.role in {"user", "assistant"}]
    return {**snapshot, "routing_state": compact}


def _emit(event):
    try:
        get_stream_writer()(event)
    except Exception:
        pass


async def route_before_reply(state, context, *, apply_decision=None):
    """Propose, then commit/verify, then select this turn's actual module.

``apply_decision(state, context, decision)`` must return committed
``current_module`` plus any refreshed memory/context. It owns its transaction;
this coordinator cannot turn a proposal or a failed commit into a module hop.
"""
    current = state.get("current_module") or state.get("extracted_intent") or "module_1"
    current = current if current in MODULES else "module_1"
    forced = state.get("forced_module")
    if forced and forced not in MODULES:
        raise ValueError(f"Unknown module {forced!r}")
    if state.get("risk") or forced or (state.get("memory") or {}).get("sandbox_mode") == "true":
        selected = current if state.get("risk") else forced or current
        return {"extracted_intent": selected, "next_module": selected,
                "routed_by": "risk_hold" if state.get("risk") else "explicit" if forced else "sandbox",
                "routing_pending": False}
    telemetry = dict(state.get("telemetry") or {})
    started = perf_counter()
    origin = state.get("turn_started_monotonic", started)
    try:
        prepared = {**state, **await load_routing_snapshot(state, context)}
    except Exception:
        logger.exception("pre-reply state read failed")
        record_span(telemetry, "state_load", started, origin=origin, status="failed")
        telemetry["router_pre_reply"] = {"status": "skipped", "reason": "state_read_failed",
            "selected_module": current, "database_module": None, "input_sources": []}
        return {"extracted_intent": current, "next_module": current, "routed_by": "state_read_failed", "telemetry": telemetry}
    record_span(telemetry, "state_load", started, origin=origin)
    current = prepared["current_module"]
    started = perf_counter()
    history = "\n".join(f"{message.role}：{message.content}" for message in
                        prepared.get("routing_history", prepared.get("chat_history", [])))
    try:
        decision = await asyncio.wait_for(decide_target_module_with_reasoning(context.router_provider,
            current_module=current, user_input=state["user_input"],
            has_pa_card=bool(prepared["routing_state"].get("has_pa_card")),
            conversation_context=history, business_state=prepared["routing_state"],
            system_prompt=context.router_prompt, max_tokens=context.settings.router_reasoning_max_tokens,
            completed_steps=(prepared.get("module_steps") or {}).get(current, [])),
            timeout=context.settings.router_request_timeout_seconds)
    except asyncio.TimeoutError:
        decision = RouterDecision(target_module=current, reasoning_content="", model="", completed_steps=[],
                                  usage={}, error_code="router_timeout")
    routing_ms = round((perf_counter() - started) * 1000, 3)
    record_span(telemetry, "router_pre_reply", started, origin=origin,
                status="failed" if decision.error_code else "completed")
    from .router_agent import ROUTER_AGENT_PROMPT, ROUTER_RUNTIME_CONTRACT
    from .knowledge_context import KNOWLEDGE_TASKS
    router_system = (context.router_prompt or ROUTER_AGENT_PROMPT) + "\n\n" + ROUTER_RUNTIME_CONTRACT
    router_system += "\nknowledge_task 可用值：" + ", ".join(KNOWLEDGE_TASKS)
    source = prompt_source("router_admin" if context.router_prompt else "router_default", router_system)
    telemetry["prompt_sources"] = [*(telemetry.get("prompt_sources") or []), source]
    if context.sessionmaker is not None and state.get("subject_id"):
        from .ai_telemetry import save_ai_event
        await save_ai_event(context.sessionmaker, stage="module_router", session_id=state["session_id"],
            subject_id=state["subject_id"], provider=context.router_provider.name,
            model_name=decision.model, duration_ms=int(routing_ms), usage=decision.usage,
            request_id=decision.request_id, finish_reason=decision.finish_reason,
            error_code=decision.error_code, prompt_version=source["version"],
            event_metadata={"phase":"pre_reply", "from_module":current,
                "proposed_module":decision.target_module, "knowledge_task":decision.knowledge_task,
                "json_recovery":decision.json_recovery, "user_message_id":state.get("user_message_id")})
    applied = {}
    authoritative = context.settings.database_schema_version == "v2" and context.sessionmaker is not None and state.get("subject_id")
    if authoritative or apply_decision is not None:
        started = perf_counter()
        try:
            if apply_decision is None:
                from .turn_confirmation import apply_pre_reply_decision
                apply_decision = apply_pre_reply_decision
            applied = await apply_decision(prepared, context, decision)
            if isinstance(applied, dict) and isinstance(applied.get("telemetry"), dict):
                hook_metrics = applied.pop("telemetry")
                observed = list(telemetry.get("execution_timeline") or [])
                for span in hook_metrics.get("execution_timeline") or []:
                    if span not in observed:
                        observed.append(span)
                telemetry.update({key:value for key,value in hook_metrics.items() if key != "execution_timeline"})
                telemetry["execution_timeline"] = observed
            if not isinstance(applied, dict) or applied.get("current_module") not in MODULES:
                raise ValueError("commit_did_not_return_persisted_module")
            record_span(telemetry, "workflow_commit", started, origin=origin)
        except Exception:
            logger.exception("pre-reply confirmation/transition failed; retaining committed state")
            # The service may have committed before a later enrichment failed.
            # Re-read the database rather than restoring an obsolete module.
            try:
                applied = await load_routing_snapshot(prepared, context)
            except Exception:
                applied = {"current_module": current}
            applied["clinical_context"] = []
            applied["diagnostics"] = {"block_reasons": ["业务状态提交或刷新失败，未把路由建议作为已提交状态"]}
            record_span(telemetry, "workflow_commit", started, origin=origin, status="failed")
    else:
        # In-memory direct graph tests have no durable business records.
        applied = {"current_module": decision.target_module}
    selected = applied["current_module"]
    if authoritative and selected == "module_3":
        # The current turn may just have committed a new recording decision;
        # do not carry the pre-commit attitude into the main reply/mediator.
        from .knowledge_context import load_recording_state
        try:
            applied.update(await load_recording_state(context.sessionmaker, state["subject_id"], state["session_id"]))
        except Exception:
            logger.warning("recording status unavailable after pre-reply commit")
            applied.update(recording_status="unknown", recording_decision_scope=None)
    diagnostics = applied.get("diagnostics") or {}
    from .knowledge_context import valid_knowledge_task
    knowledge_task = decision.knowledge_task if valid_knowledge_task(decision.knowledge_task, selected) else "general"
    telemetry["router_duration_ms"] = routing_ms
    telemetry["router_pre_reply"] = {
        "input_sources": ["当前用户输入", "此前对话", "已提交的紧凑业务状态"],
        "selected_module": selected, "database_module": selected if authoritative else None,
        "database_module_before": current if authoritative else None,
        "proposed_module": decision.target_module, "knowledge_task": knowledge_task,
        "status": "failed" if decision.error_code else "completed",
        "reason": decision.error_code or "；".join(diagnostics.get("block_reasons") or []),
        "duration_ms": routing_ms, "retry_count": 1 if decision.json_recovery else 0,
    }
    reasoning = format_routing_reasoning(decision, current, selected, diagnostics=diagnostics)
    _emit({"type": "meta", "node": "pre_reply_router", "reply_module": selected, "routed_by": "pre_reply_router"})
    _emit({"type": "routing_reasoning", "text": reasoning, "model": decision.model})
    _emit({"type": "trace", "node": "pre_reply_router", "detail": telemetry["router_pre_reply"]})
    return {**{key: value for key, value in prepared.items() if key in {"current_module", "memory", "active_cycle_id", "routing_state", "recording_status", "recording_decision_scope"}},
            **{key: value for key, value in applied.items() if key != "diagnostics"},
            "transition_from_module": current, "knowledge_task": knowledge_task,
            "extracted_intent": selected, "next_module": selected, "routed_by": "pre_reply_router",
            "routing_reasoning_content": reasoning, "router_model_name": decision.model, "telemetry": telemetry}


async def pre_reply_router_node(state, runtime):
    return await route_before_reply(state, runtime.context)


def route_after_pre_reply(state):
    return state.get("extracted_intent") if state.get("extracted_intent") in MODULES else "module_1"


def route_to_pre_reply(state):
    return "crisis" if state.get("risk") else "pre_reply_router"
