"""Transactional V2 goal/plan/cycle repository.

All entry points receive the authenticated user ID. These primitives neither
commit nor call an LLM: caller owns the transaction and can audit atomically.
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .database_v2_schema import metadata


class V2Conflict(ValueError):
    pass


class V2NotFound(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id():
    return str(uuid.uuid4())


def can_reuse_m1(state) -> bool:
    """Reuse explicit confirmation OR the approved legacy completion import.

    Revisit-needed does not force a return to M1. Empty imported step lists are
    intentionally not expanded into invented evidence for individual steps.
    """
    if not state or state["status"] not in {"completed", "revisit_needed"}:
        return False
    if state["completion_source"] == "legacy_imported":
        return state["evidence_status"] == "missing" and state["confirmed_formulation_id"] is None
    return (state["completion_source"] == "user_confirmed"
            and state["evidence_status"] == "available"
            and state["confirmed_formulation_id"] is not None)


async def initial_module(db: AsyncSession, *, user_id: str) -> str:
    states = metadata.tables["user_module_one_state"]
    state = (await db.execute(select(states).where(states.c.user_id == user_id))).mappings().one_or_none()
    return "module_2" if can_reuse_m1(state) else "module_1"


async def check_conversation_owner(db: AsyncSession, user_id: str, conversation_id: int | None):
    if conversation_id is None:
        return
    from .models import Conversation
    found = (await db.execute(select(Conversation.id).where(
        Conversation.id == conversation_id, Conversation.subject_id == user_id))).scalar_one_or_none()
    if found is None:
        raise V2NotFound("聊天不存在")


async def owned_goal(db: AsyncSession, user_id: str, goal_id: str, *, lock=False):
    goals = metadata.tables["pa_goals"]
    query = select(goals).where(goals.c.id == goal_id, goals.c.user_id == user_id)
    if lock:
        query = query.with_for_update()
    row = (await db.execute(query)).mappings().one_or_none()
    if row is None:
        raise V2NotFound("目标不存在")
    return row


async def create_goal(db: AsyncSession, *, user_id: str, title: str,
                      conversation_id: int | None = None):
    title = title.strip()
    if not title or len(title) > 255:
        raise V2Conflict("目标标题需为 1–255 字")
    await check_conversation_owner(db, user_id, conversation_id)
    m1 = metadata.tables["user_module_one_state"]
    state = (await db.execute(select(m1).where(m1.c.user_id == user_id))).mappings().one_or_none()
    goal_id = new_id()
    await db.execute(insert(metadata.tables["pa_goals"]), {
        "id": goal_id, "user_id": user_id, "title": title,
        "module_one_record_id": state["confirmed_formulation_id"] if state else None,
        "created_from_conversation_id": conversation_id,
    })
    return goal_id


async def start_cycle(db: AsyncSession, *, user_id: str, goal_id: str,
                      conversation_id: int | None = None):
    # Lock the parent even when there are no children, preventing ordinal races.
    await check_conversation_owner(db, user_id, conversation_id)
    goal = await owned_goal(db, user_id, goal_id, lock=True)
    if goal["status"] not in {"draft", "active"}:
        raise V2Conflict("目标已暂停或结束，请先明确恢复或选择其他目标")
    cycles = metadata.tables["pa_cycles"]
    active = (await db.execute(select(cycles.c.id).where(
        cycles.c.goal_id == goal_id, cycles.c.status.in_(["planning", "waiting_execution", "reviewing"])
    ))).scalars().all()
    if active:
        raise V2Conflict("该目标已有未结束周期，请继续该周期")
    ordinal = int((await db.execute(select(func.max(cycles.c.ordinal)).where(
        cycles.c.goal_id == goal_id))).scalar_one() or 0) + 1
    cycle_id = new_id()
    await db.execute(insert(cycles), {"id": cycle_id, "goal_id": goal_id, "ordinal": ordinal,
        "started_from_conversation_id": conversation_id})
    await db.execute(insert(metadata.tables["pa_cycle_progress"]), {
        "cycle_id": cycle_id, "module_2_steps": [], "module_3_steps": [], "module_4_steps": []})
    return cycle_id


async def append_plan_draft(db: AsyncSession, *, user_id: str, goal_id: str, fields: dict):
    goal = await owned_goal(db, user_id, goal_id, lock=True)
    if goal["status"] not in {"draft", "active"}:
        raise V2Conflict("目标已暂停或结束，不能自动创建计划版本")
    plans = metadata.tables["module_two_record"]
    writable = {"pa_understanding_status", "pa_willingness_status", "core_values",
        "core_values_impact", "activity_content", "schedule_text", "scheduled_start_at",
        "timezone", "location", "duration_minutes", "frequency_rule", "companion",
        "potential_barriers", "barrier_coping_plan"}
    if fields.keys() - writable:
        raise V2Conflict("不允许通过计划内容修改归属、确认状态或版本")
    version = int((await db.execute(select(func.max(plans.c.version_no)).where(
        plans.c.goal_id == goal_id))).scalar_one() or 0) + 1
    plan_id = new_id()
    await db.execute(insert(plans), {"id": plan_id, "goal_id": goal_id, "version_no": version,
                                    "timezone": "Asia/Shanghai", **fields})
    return plan_id


async def check_user_message(db: AsyncSession, *, user_id: str, message_id: int):
    # Application-owned transcript tables are shared between schema versions.
    from .models import Conversation, ConversationMessage
    found = (await db.execute(select(ConversationMessage.id).join(
        Conversation, Conversation.id == ConversationMessage.conversation_id).where(
        Conversation.subject_id == user_id, ConversationMessage.id == message_id,
        ConversationMessage.role == "user"))).scalar_one_or_none()
    if found is None:
        raise V2Conflict("确认必须关联本人的用户消息，不能使用 AI 回复作为确认")


async def confirm_plan(db: AsyncSession, *, user_id: str, goal_id: str, cycle_id: str,
                       plan_id: str, message_id: int):
    goal = await owned_goal(db, user_id, goal_id, lock=True)
    if goal["status"] not in {"draft", "active"}:
        raise V2Conflict("目标已暂停或结束，不能通过确认计划自动恢复")
    await check_user_message(db, user_id=user_id, message_id=message_id)
    plans, cycles = metadata.tables["module_two_record"], metadata.tables["pa_cycles"]
    plan = (await db.execute(select(plans).where(plans.c.id == plan_id,
        plans.c.goal_id == goal_id).with_for_update())).mappings().one_or_none()
    cycle = (await db.execute(select(cycles).where(cycles.c.id == cycle_id,
        cycles.c.goal_id == goal_id).with_for_update())).mappings().one_or_none()
    if not plan or not cycle:
        raise V2NotFound("目标版本或周期不存在")
    if cycle["status"] != "planning" or plan["record_status"] != "draft":
        raise V2Conflict("只有规划中的周期和草稿计划可以确认")
    from .plan_contract import missing_plan_fields
    missing = missing_plan_fields(plan)
    if missing:
        raise V2Conflict("计划信息尚未完整，请继续明确活动、时间、地点、时长、频率及障碍应对：" + ", ".join(missing))
    await db.execute(update(plans).where(plans.c.id == plan_id).values(
        record_status="confirmed", confirmation_status="confirmed", confirmation_message_id=message_id,
        updated_at=now()))
    # Never modify an older confirmed plan: historical cycles still reference it.
    goals = metadata.tables["pa_goals"]
    await db.execute(update(goals).where(goals.c.id == goal_id).values(
        current_plan_record_id=plan_id, status="active", updated_at=now(), row_version=goal["row_version"] + 1))
    await db.execute(update(cycles).where(cycles.c.id == cycle_id).values(
        module_two_record_id=plan_id, updated_at=now()))


async def transition_cycle(db: AsyncSession, *, user_id: str, goal_id: str,
                           cycle_id: str, target: str):
    await owned_goal(db, user_id, goal_id, lock=True)
    cycles = metadata.tables["pa_cycles"]
    cycle = (await db.execute(select(cycles).where(cycles.c.id == cycle_id,
        cycles.c.goal_id == goal_id).with_for_update())).mappings().one_or_none()
    if cycle is None:
        raise V2NotFound("周期不存在")
    allowed = {"planning": {"waiting_execution", "cancelled"},
               "waiting_execution": {"reviewing", "cancelled"},
               "reviewing": {"completed", "cancelled"}}
    if target not in allowed.get(cycle["status"], set()):
        raise V2Conflict("不允许的周期状态变更")
    if target != "cancelled":
        plans = metadata.tables["module_two_record"]
        valid = (await db.execute(select(plans.c.id).where(
            plans.c.id == cycle["module_two_record_id"], plans.c.goal_id == goal_id,
            plans.c.record_status == "confirmed"))).scalar_one_or_none()
        if valid is None:
            raise V2Conflict("周期缺少同一目标下的已确认计划")
    if target == "waiting_execution":
        contracts = metadata.tables["module_three_record"]
        valid = (await db.execute(select(contracts.c.id).where(
            contracts.c.id == cycle["module_three_record_id"], contracts.c.goal_id == goal_id,
            contracts.c.module_two_record_id == cycle["module_two_record_id"],
            contracts.c.record_status == "confirmed"))).scalar_one_or_none()
        if valid is None:
            raise V2Conflict("执行前需要确认本版本的记录契约")
    if target == "completed":
        reviews = metadata.tables["module_four_record"]
        valid = (await db.execute(select(reviews.c.id).where(
            reviews.c.cycle_id == cycle_id, reviews.c.record_status == "confirmed"))).scalar_one_or_none()
        if valid is None:
            raise V2Conflict("结束周期前需要确认复盘结果")
    await db.execute(update(cycles).where(cycles.c.id == cycle_id).values(
        status=target, updated_at=now(), **({"completed_at": now()} if target == "completed" else {})))


async def append_memory(db: AsyncSession, *, user_id: str, memory_type: str, content: str,
                        source_kind: str, source_message_id: int | None = None,
                        supersedes_id: str | None = None):
    memories = metadata.tables["ba_memory"]
    profiles = metadata.tables["user_profile"]
    owner = (await db.execute(select(profiles.c.uuid).where(
        profiles.c.uuid == user_id).with_for_update())).scalar_one_or_none()
    if owner is None:
        raise V2NotFound("档案不存在")
    if not content.strip() or len(content) > 8000 or not memory_type or len(memory_type) > 32:
        raise V2Conflict("记忆内容或类别无效")
    if source_kind not in {"user_statement", "user_confirmation", "ai_inference", "imported"}:
        raise V2Conflict("未知记忆来源")
    if source_message_id is not None:
        await check_user_message(db, user_id=user_id, message_id=source_message_id)
    if source_kind in {"user_statement", "user_confirmation"} and source_message_id is None:
        raise V2Conflict("用户来源记忆必须保留证据消息")
    old = None
    if supersedes_id:
        old = (await db.execute(select(memories).where(memories.c.id == supersedes_id,
            memories.c.user_id == user_id).with_for_update())).mappings().one_or_none()
        if not old or old["status"] != "active":
            raise V2Conflict("待修正的记忆不存在或已被替代")
    # Exact repeats in one source message do not count as independent evidence.
    existing = (await db.execute(select(memories.c.id).where(
        memories.c.user_id == user_id, memories.c.memory_type == memory_type,
        memories.c.content == content.strip(), memories.c.source_message_id == source_message_id,
        memories.c.source_kind == source_kind, memories.c.status == "active"))).scalar_one_or_none()
    if existing:
        if old and existing != old["id"]:
            raise V2Conflict("修正内容与另一条有效记忆相同，请明确合并关系")
        return existing
    memory_id = new_id()
    await db.execute(insert(memories), {"id": memory_id, "user_id": user_id,
        "memory_type": memory_type, "memory_key": old["memory_key"] if old else new_id(),
        "content": content.strip(), "source_kind": source_kind, "source_message_id": source_message_id,
        "confirmation_status": "confirmed" if source_kind == "user_confirmation" else "unconfirmed",
        "supersedes_id": supersedes_id})
    if old:
        await db.execute(update(memories).where(memories.c.id == old["id"]).values(status="superseded", updated_at=now()))
    return memory_id


async def active_memories(db: AsyncSession, *, user_id: str):
    memories = metadata.tables["ba_memory"]
    return (await db.execute(select(memories).where(memories.c.user_id == user_id,
        memories.c.status == "active", memories.c.confirmation_status != "rejected",
        (memories.c.valid_from.is_(None) | (memories.c.valid_from <= now())),
        (memories.c.valid_until.is_(None) | (memories.c.valid_until > now()))
    ).order_by(memories.c.created_at))).mappings().all()
