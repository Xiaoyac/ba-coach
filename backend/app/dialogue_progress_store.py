"""Conversation-local M3 evidence; never commits a plan, consent or transition."""
from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy import select, update

from .database_v2_schema import metadata
from .dialogue_progress import build_m3_progress, render_m3_progress
from .models import ConversationMessage
from .v2_repository import PLAN_WRITABLE_FIELDS, now

MEMORY_KEY = "m3_dialogue_progress"


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def progress_scope(db, *, conversation, state):
    """Resolve owned goal/cycle and the material plan version, including drafts."""
    goals, cycles, plans = (metadata.tables[name] for name in
                           ("pa_goals", "pa_cycles", "module_two_record"))
    goal_id, cycle_id = state.get("active_goal_id"), state.get("active_cycle_id")
    goal = cycle = plan = None
    if goal_id:
        goal = (await db.execute(select(goals).where(
            goals.c.id == goal_id, goals.c.user_id == conversation.subject_id))).mappings().one_or_none()
        if not goal:
            return None, None
    if cycle_id:
        cycle = (await db.execute(select(cycles).where(
            cycles.c.id == cycle_id, cycles.c.goal_id == goal_id))).mappings().one_or_none()
        if not cycle:
            return None, None
    if goal:
        plan_id = cycle.get("module_two_record_id") if cycle else None
        query = select(plans).where(plans.c.goal_id == goal_id)
        query = query.where(plans.c.id == plan_id) if plan_id else query.where(plans.c.record_status == "draft")
        plan = (await db.execute(query.order_by(plans.c.version_no.desc()).limit(1))).mappings().one_or_none()
        if plan_id and not plan:
            return None, None
    # Confirmation timestamps/flags alone do not change the plan the user saw.
    payload = ({key: plan[key] for key in ("id", "version_no", *sorted(PLAN_WRITABLE_FIELDS))}
               if plan else None)
    version = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str,
                                        ensure_ascii=False).encode()).hexdigest()
    dates = [_utc(row.get(key)) for row, key in
             ((goal, "created_at"), (cycle, "created_at"), (plan, "updated_at")) if row]
    floor = max((value for value in dates if value is not None), default=None)
    return dict(session_id=conversation.session_id, goal_id=goal_id,
                cycle_id=cycle_id, plan_version=version), floor


async def save_m3_progress(db, *, conversation, state, data, messages, assistant_message_id):
    """Caller holds the turn lock and commits. Keep all other runtime memory."""
    if state.get("current_module") != "module_3" or not isinstance(data, dict):
        return {"status": "skipped", "reason_code": "no_m3_extraction"}
    if any(message.conversation_id != conversation.id for message in messages):
        return {"status": "skipped", "reason_code": "foreign_message_source"}
    latest = (await db.execute(select(ConversationMessage.id).where(
        ConversationMessage.conversation_id == conversation.id).order_by(
        ConversationMessage.position.desc()).limit(1))).scalar_one_or_none()
    if latest != assistant_message_id:
        return {"status": "skipped", "reason_code": "stale_message_boundary"}
    scope, floor = await progress_scope(db, conversation=conversation, state=state)
    if scope is None:
        return {"status": "skipped", "reason_code": "invalid_progress_scope"}
    memory = dict(state.get("memory") or {})
    previous = memory.get(MEMORY_KEY)
    same = isinstance(previous, dict) and all(previous.get(k) == v for k, v in scope.items())
    if same and type(previous.get("scope_start_position")) is int:
        start = previous["scope_start_position"]
    elif previous:
        # A changed plan must not repackage old acceptance under its new hash.
        users = [m for m in messages if m.role == "user"]
        start = users[-1].position if users else messages[-1].position
        eligible = [m.position for m in messages if floor is None or
                    (_utc(m.created_at) is not None and _utc(m.created_at) >= floor)]
        start = max(start, min(eligible, default=messages[-1].position))
    else:
        eligible = [m.position for m in messages if floor is None or
                    (_utc(m.created_at) is not None and _utc(m.created_at) >= floor)]
        start = min(eligible, default=messages[-1].position if messages else 0)
    scoped = [m for m in messages if m.position >= start]
    progress = build_m3_progress(data, scoped, **scope,
                                assistant_message_id=assistant_message_id, previous=previous)
    if not progress["verified"]:
        return {"status": "skipped", "reason_code": "invalid_message_boundary"}
    if not progress["evidence"] and not previous:
        return {"status": "skipped", "reason_code": "no_verified_evidence"}
    progress["scope_start_position"] = start
    memory[MEMORY_KEY] = progress
    runtime = metadata.tables["conversation_runtime_states"]
    changed = await db.execute(update(runtime).where(
        runtime.c.conversation_id == conversation.id,
        runtime.c.row_version == state["row_version"],
        runtime.c.current_module == "module_3").values(
            memory=memory, row_version=runtime.c.row_version + 1, updated_at=now()))
    if changed.rowcount != 1:
        return {"status": "skipped", "reason_code": "stale_runtime_version"}
    return {"status": "completed", "reason_code": "dialogue_evidence_saved",
            "source_message_id": assistant_message_id,
            "evidence_fields": sorted(progress["evidence"])}


async def read_m3_progress(db, *, conversation, state, latest_user_message_id=None):
    """Return revalidated historical evidence for the current scope only."""
    if state.get("current_module") != "module_3":
        return ""
    progress = (state.get("memory") or {}).get(MEMORY_KEY)
    if not isinstance(progress, dict) or not progress.get("verified"):
        return ""
    scope, _ = await progress_scope(db, conversation=conversation, state=state)
    if scope is None or any(progress.get(k) != v for k, v in scope.items()):
        return ""
    boundary = await db.get(ConversationMessage, progress.get("assistant_message_id"))
    if not boundary or boundary.conversation_id != conversation.id or boundary.role != "assistant":
        return ""
    messages = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.position >= progress.get("scope_start_position", 0),
        ConversationMessage.position <= boundary.position).order_by(
        ConversationMessage.position))).scalars().all()
    evidence = progress.get("evidence") or {}
    verified = build_m3_progress({"recording_evidence": evidence,
        "recording_status": (evidence.get("decision") or {}).get("status", "unknown")},
        messages, **scope, assistant_message_id=boundary.id)
    return render_m3_progress(verified, **scope, latest_user_message_id=latest_user_message_id)
