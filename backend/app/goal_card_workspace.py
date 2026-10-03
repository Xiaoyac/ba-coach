"""Owned, versioned M2 formulation cards; form input is never a confirmation.

No helper commits a transaction or changes a module. Callers hold the normal
conversation/profile lock and commit the card plus business operation together.
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import delete, inspect, insert, select, update

from .database_v2_schema import metadata as schema
from .models import ConversationMessage
from .v2_repository import new_id, now


class CardFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activity_content: str | None = Field(default=None, max_length=255)
    schedule_text: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    duration_minutes: StrictInt | None = Field(default=None, ge=0, le=1440)
    frequency_text: str | None = Field(default=None, max_length=255)
    difficulty_rating: StrictInt | None = Field(default=None, ge=0, le=10)
    potential_barriers: str | None = Field(default=None, max_length=2000)
    barrier_coping_plan: str | None = Field(default=None, max_length=4000)
    long_term_direction: str | None = Field(default=None, max_length=1000)


class CardSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: str = Field(min_length=1, max_length=36)
    revision: StrictInt = Field(ge=1)
    fields: CardFields


def public_card(row):
    if not row:
        return None
    return {key: row[key] for key in (
        "id", "kind", "phase", "revision", "fields", "concerns", "goal_id", "plan_id")}


async def read_card_row(db, conversation_id, user_id, card_id=None, *, lock=False):
    table = schema.tables["goal_card_workspaces"]
    query = select(table).where(table.c.conversation_id == conversation_id, table.c.user_id == user_id)
    if card_id is not None:
        query = query.where(table.c.id == card_id)
    query = query.order_by(table.c.updated_at.desc(), table.c.created_at.desc(), table.c.id.desc()).limit(1)
    if lock:
        query = query.with_for_update()
    row = (await db.execute(query)).mappings().one_or_none()
    return dict(row) if row else None


async def latest_card(db, conversation_id, user_id):
    return public_card(await read_card_row(db, conversation_id, user_id))


def _check_scope(conversation, state, *, m2=True):
    if (not conversation.subject_id or state.get("conversation_id") != conversation.id
            or (m2 and state.get("current_module") != "module_2")
            or state.get("flow_status") in {"paused", "completed"}
            or str((state.get("memory") or {}).get("sandbox_mode", "")).lower() == "true"):
        raise HTTPException(409, "当前不在可编辑的目标制定阶段")


async def _source(db, conversation, message_id, *, require_latest=False,
                  following_assistant_message_id=None):
    source = await db.get(ConversationMessage, message_id)
    if not source or source.conversation_id != conversation.id or source.role != "user":
        raise HTTPException(409, "卡片操作必须对应当前聊天的真实用户消息")
    if require_latest:
        latest = (await db.execute(select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id).order_by(
                ConversationMessage.position.desc(), ConversationMessage.id.desc()).limit(1))).scalar_one_or_none()
        if following_assistant_message_id is not None:
            valid = bool(latest and latest.id == following_assistant_message_id
                and latest.role == "assistant" and latest.position == source.position + 1)
        else:
            valid = bool(latest and latest.id == message_id)
        if not valid:
            raise HTTPException(409, "对话已更新，请重新读取当前卡片")
    return source


async def _audit(db, row, reason, message_id=None):
    await db.execute(insert(schema.tables["ai_decision_logs"]), {
        "conversation_id": row["conversation_id"], "turn_id": str(message_id or "goal-card-form"),
        "goal_id": row.get("goal_id"), "cycle_id": row.get("cycle_id"),
        "module_name": "module_2", "decision_type": "goal_card_revision",
        "decision_value": json.loads(json.dumps(dict(row), ensure_ascii=False, default=str)),
        "reason_summary": reason, "evidence_message_ids": [message_id] if message_id else [],
    })


async def record_card_update(db, row, changes, *, reason, message_id=None):
    """Version-checked projection update with an immutable snapshot receipt."""
    table = schema.tables["goal_card_workspaces"]
    if set(changes) & {"id", "user_id", "conversation_id", "revision", "created_at"}:
        raise ValueError("card_identity_is_immutable")
    if all(row.get(key) == value for key, value in changes.items()):
        return dict(row)
    values = {**changes, "revision": row["revision"] + 1, "updated_at": now()}
    result = await db.execute(update(table).where(table.c.id == row["id"],
        table.c.user_id == row["user_id"], table.c.revision == row["revision"]).values(**values))
    if result.rowcount != 1:
        raise HTTPException(409, "目标卡已更新，请重新查看后再操作")
    fresh = {**row, **values}
    await _audit(db, fresh, reason, message_id)
    return fresh


async def _select_card(db, row, message_id):
    latest = await read_card_row(db, row["conversation_id"], row["user_id"], lock=True)
    if latest and latest["id"] != row["id"]:
        # Selection does not alter card contents or invalidate a review of the
        # exact same version. It only chooses which workspace GET presents.
        table = schema.tables["goal_card_workspaces"]
        stamp = now()
        await db.execute(update(table).where(table.c.id == row["id"]).values(updated_at=stamp))
        await _audit(db, {**dict(row), "updated_at": stamp}, "card_reselected", message_id)
    return public_card(row)


async def open_card(db, conversation, state, kind, user_message_id, *, following_assistant_message_id=None):
    _check_scope(conversation, state)
    if kind not in {"primary", "secondary"}:
        raise HTTPException(422, "目标卡类型无效")
    await _source(db, conversation, user_message_id, require_latest=True,
                  following_assistant_message_id=following_assistant_message_id)
    table = schema.tables["goal_card_workspaces"]
    # Repeated native calls in one turn are idempotent, including a completed
    # card. An unfinished card is reused rather than repeatedly popped open.
    rows = (await db.execute(select(table).where(table.c.conversation_id == conversation.id,
        table.c.user_id == conversation.subject_id, table.c.kind == kind).order_by(
            table.c.updated_at.desc(), table.c.id.desc()).with_for_update())).mappings().all()
    for prior in rows:
        if prior["opened_message_id"] == user_message_id:
            return await _select_card(db, prior, user_message_id)
        if prior["phase"] != "confirmed" and (
                kind == "secondary" or prior["cycle_id"] in {None, state.get("active_cycle_id")}):
            if prior["phase"] == "paused":
                return public_card(await record_card_update(db, dict(prior), {"phase": "formulating",
                    "review": None, "concerns": [], "display_text": None, "display_user_message_id": None,
                    "display_assistant_message_id": None, "review_message_id": None, "review_display_text": None},
                    reason="user_resumed_formulation", message_id=user_message_id))
            return await _select_card(db, prior, user_message_id)
    row = {"id": new_id(), "user_id": conversation.subject_id, "conversation_id": conversation.id,
        "kind": kind, "phase": "formulating", "revision": 1, "fields": {}, "concerns": [],
        "goal_id": None, "plan_id": None, "cycle_id": None,
        "opened_message_id": user_message_id, "submission_text": None, "submission_message_id": None,
        "review_message_id": None, "review_display_text": None, "confirmation_message_id": None,
        "review": None, "display_text": None, "display_user_message_id": None, "display_assistant_message_id": None,
        "created_at": now(), "updated_at": now()}
    if kind == "primary" and state.get("active_goal_id") and state.get("active_cycle_id"):
        from .program_confirmation import draft
        pending = await draft(db, state, conversation.subject_id)
        if pending and pending["record_status"] == "draft" and pending["goal_id"] == state["active_goal_id"]:
            row.update(fields=_plan_fields(pending), plan_id=pending["id"],
                goal_id=pending["goal_id"], cycle_id=state["active_cycle_id"])
    await db.execute(insert(table), row)
    await _audit(db, row, "entered_formulation_stage", user_message_id)
    return public_card(row)


LABELS = {"activity_content": "活动", "schedule_text": "时间安排", "location": "地点",
    "duration_minutes": "时长（分钟）", "frequency_text": "频率", "difficulty_rating": "我评估的执行难度（0–10）",
    "potential_barriers": "可能的困难", "barrier_coping_plan": "我想到的应对方式", "long_term_direction": "我希望改善的方向"}


def submission_text(card_id, revision, kind, fields):
    label = "核心" if kind == "primary" else "次要"
    lines = [f"我填写了{label} PA 目标卡草稿，请根据这些信息和之前的对话一起讨论、细化；这还不是最终确认。",
        f"这是当前目标卡的第 {revision} 版草稿。"]
    for key, label in LABELS.items():
        value = fields.get(key)
        if value is not None and value != "":
            lines.append(f"{label}：{value}")
    lines.append("未填写的部分表示暂时没有补充，不代表否定之前说过的信息，也不要替我编造。")
    return "\n".join(lines)


async def save_card_fields(db, conversation, state, payload):
    _check_scope(conversation, state)
    row = await read_card_row(db, conversation.id, conversation.subject_id, payload.card_id, lock=True)
    if not row:
        raise HTTPException(404, "目标卡不存在")
    if row["revision"] != payload.revision or row["phase"] not in {"formulating", "discussing", "ready"}:
        raise HTTPException(409, "目标卡已更新或已结束，请重新查看后再操作")
    if row["kind"] == "primary" and row["cycle_id"] not in {None, state.get("active_cycle_id")}:
        raise HTTPException(409, "当前计划已变化，请重新查看目标卡")
    fields = payload.fields.model_dump()
    text = submission_text(row["id"], row["revision"] + 1, row["kind"], fields)
    fresh = await record_card_update(db, row, {"fields": fields, "phase": "discussing", "concerns": [],
        "submission_text": text, "submission_message_id": None, "review_message_id": None,
        "review_display_text": None, "confirmation_message_id": None,
        "review": None, "display_text": None, "display_user_message_id": None,
        "display_assistant_message_id": None}, reason="user_edited_form")
    conversation.revision += 1
    return {"enabled": True, "card": public_card(fresh), "submission_text": text}


async def bind_submission(db, conversation, message_id):
    source = await _source(db, conversation, message_id, require_latest=True)
    row = await read_card_row(db, conversation.id, conversation.subject_id, lock=True)
    if not row or row["phase"] != "discussing" or row["submission_text"] != source.content:
        return None
    if row["submission_message_id"] not in {None, message_id}:
        return None
    # Binding proves source identity without changing the displayed content
    # revision; an old canonical text cannot bind to a newly edited version.
    if row["submission_message_id"] is None:
        table = schema.tables["goal_card_workspaces"]
        await db.execute(update(table).where(table.c.id == row["id"], table.c.revision == row["revision"]).values(
            submission_message_id=message_id))
        await _audit(db, {**row, "submission_message_id": message_id}, "form_bound_to_user_message", message_id)
    return public_card(row)


def _plan_fields(record):
    direct = {key: record[key] for key in ("activity_content", "schedule_text", "location", "duration_minutes", "difficulty_rating")
        if record.get(key) is not None}
    frequency = record.get("frequency_rule")
    if isinstance(frequency, dict) and frequency.get("text"):
        direct["frequency_text"] = frequency["text"]
    for key in ("potential_barriers", "barrier_coping_plan"):
        if key == "barrier_coping_plan":
            from .plan_contract import no_coping_required, NO_COPING_NEEDED
            if no_coping_required(record):
                direct[key] = NO_COPING_NEEDED
                continue
        value = record.get(key)
        if isinstance(value, list) and value:
            direct[key] = "\n".join(str(v) if isinstance(v, str) else f"{v.get('barrier', '')}：{v.get('plan', '')}" for v in value)
        elif isinstance(value, str) and value:
            direct[key] = value
    return direct


async def sync_core_card(db, conversation, state, phase, record):
    _check_scope(conversation, state, m2=False)
    if phase not in {"discussing", "ready", "confirmed"} or not isinstance(record, Mapping):
        raise ValueError("invalid_card_projection")
    plans, goals = schema.tables["module_two_record"], schema.tables["pa_goals"]
    actual = (await db.execute(select(plans).join(goals, goals.c.id == plans.c.goal_id).where(
        plans.c.id == record.get("id"), goals.c.user_id == conversation.subject_id,
        plans.c.goal_id == state.get("active_goal_id")))).mappings().one_or_none()
    if actual is None:
        return None
    record = actual
    table = schema.tables["goal_card_workspaces"]
    row = (await db.execute(select(table).where(table.c.user_id == conversation.subject_id,
        table.c.conversation_id == conversation.id, table.c.kind == "primary",
        table.c.phase != "confirmed").order_by(table.c.updated_at.desc(), table.c.id.desc()).limit(1)
        .with_for_update())).mappings().one_or_none()
    if not row or row["phase"] == "paused":
        return None
    if row["cycle_id"] not in {None, state.get("active_cycle_id")}:
        return None
    record_goal = record.get("goal_id")
    if record_goal != state.get("active_goal_id") or (row["goal_id"] not in {None, record_goal}):
        return None
    # Confirmed is an observed database result, never a model-proposed phase.
    if phase == "confirmed" and (record.get("record_status") != "confirmed"
            or record.get("confirmation_status") != "confirmed" or not record.get("confirmation_message_id")):
        raise ValueError("plan_not_confirmed")
    fields = {**(row["fields"] or {}), **_plan_fields(record)}
    changes = {"fields": fields, "phase": phase, "goal_id": record_goal, "plan_id": record.get("id"),
        "cycle_id": state.get("active_cycle_id"), "concerns": []}
    if phase == "confirmed":
        changes["confirmation_message_id"] = record["confirmation_message_id"]
    if fields != row["fields"] or record.get("id") != row["plan_id"]:
        changes.update(review=None, display_text=None, display_user_message_id=None,
            display_assistant_message_id=None, review_message_id=None, review_display_text=None)
    if all(row.get(k) == v for k, v in changes.items()):
        return public_card(row)
    return public_card(await record_card_update(db, dict(row), changes, reason="business_plan_" + phase,
        message_id=record.get("confirmation_message_id") if phase == "confirmed" else None))


async def pause_card(db, conversation, state, user_message_id=None, *, following_assistant_message_id=None):
    _check_scope(conversation, state)
    if user_message_id is not None:
        await _source(db, conversation, user_message_id, require_latest=True,
                      following_assistant_message_id=following_assistant_message_id)
    row = await read_card_row(db, conversation.id, conversation.subject_id, lock=True)
    if not row or row["phase"] in {"confirmed", "paused"}:
        return public_card(row)
    return public_card(await record_card_update(db, row, {"phase": "paused", "review_message_id": None,
        "review_display_text": None, "review": None, "display_text": None, "display_user_message_id": None,
        "display_assistant_message_id": None}, reason="user_paused_formulation", message_id=user_message_id))


async def confirmed_secondary_cards(db, user_id):
    table = schema.tables["goal_card_workspaces"]
    rows = (await db.execute(select(table).where(table.c.user_id == user_id,
        table.c.kind == "secondary", table.c.phase == "confirmed").order_by(table.c.updated_at.desc()))).mappings()
    return [public_card(row) for row in rows]


async def workspace_table_exists(db):
    """Deletion also works before migration and after disabling the UI flag."""
    return await db.run_sync(lambda session: inspect(session.connection()).has_table("goal_card_workspaces"))


async def delete_conversation_cards(db, conversation):
    if not await workspace_table_exists(db):
        return
    table = schema.tables["goal_card_workspaces"]
    scope = (table.c.conversation_id == conversation.id) & (table.c.user_id == conversation.subject_id)
    # Primary cards are projections of ordinary goals, whose existing source-
    # scoped deletion/retention rules continue to govern the clinical archive.
    await db.execute(delete(table).where(scope,
        (table.c.kind == "primary") | (table.c.phase != "confirmed")))
    # Confirmed secondary activities are the actual lightweight archive. Keep
    # their user-owned facts but detach deleted transcript and inference data.
    await db.execute(update(table).where(scope).values(conversation_id=None,
        goal_id=None, cycle_id=None, plan_id=None, submission_text=None, submission_message_id=None,
        review_message_id=None, review_display_text=None, review=None, display_text=None,
        display_user_message_id=None, display_assistant_message_id=None,
        revision=table.c.revision + 1, updated_at=now()))


async def delete_user_cards(db, user_id):
    if await workspace_table_exists(db):
        table = schema.tables["goal_card_workspaces"]
        await db.execute(delete(table).where(table.c.user_id == user_id))
