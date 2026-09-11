"""Authenticated V2 programme selection and explicit record confirmation."""
import hashlib
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, insert, update, func
from ..db import get_db
from ..identity import require_subject_id
from ..database_v2_schema import metadata as schema
from ..models import ConversationMessage
from ..session import get_session_store
from ..v2_profile import enabled
from ..v2_repository import create_goal, start_cycle, owned_goal, initial_module, confirm_plan, V2Conflict, V2NotFound, now
from ..v2_workflow import runtime_for, TABLES
from ..workflow_contract import MODULE_STEP_KEYS

router = APIRouter(prefix="/program", tags=["program"])


class Selection(BaseModel):
    goal_id: str | None = Field(default=None, max_length=36)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    row_version: int = Field(ge=0)


class Confirmation(BaseModel):
    record_id: str = Field(min_length=1, max_length=36)
    record_hash: str = Field(min_length=64, max_length=64)
    row_version: int = Field(ge=0)


def record_hash(row):
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


async def own_state(db, session_id, user_id):
    if not enabled():
        raise HTTPException(404, "V2 尚未启用")
    conversation, state = await runtime_for(db, session_id)
    if not conversation or conversation.subject_id != user_id or not state:
        raise HTTPException(404, "聊天不存在")
    if state["memory"].get("sandbox_mode") == "true":
        raise HTTPException(409, "测试沙盒不写入正式目标")
    return conversation, state


async def draft(db, state, user_id):
    table = schema.tables[TABLES[state["current_module"]]]
    if state["current_module"] == "module_1":
        scope = table.c.user_id == user_id
    elif state["current_module"] == "module_4":
        scope = table.c.cycle_id == state["active_cycle_id"]
    else:
        scope = table.c.goal_id == state["active_goal_id"]
        if state["current_module"] == "module_3":
            cycles = schema.tables["pa_cycles"]
            plan = (await db.execute(select(cycles.c.module_two_record_id).where(
                cycles.c.id == state["active_cycle_id"], cycles.c.goal_id == state["active_goal_id"]))).scalar_one_or_none()
            scope = scope & (table.c.module_two_record_id == plan)
    return (await db.execute(select(table).where(scope, table.c.record_status == "draft")
        .order_by(table.c.created_at.desc()).limit(1))).mappings().one_or_none()


@router.get("/{session_id}")
async def get_program(session_id: str, user_id=Depends(require_subject_id), db=Depends(get_db)):
    if not enabled():
        return {"enabled": False}
    conversation, state = await own_state(db, session_id, user_id)
    goals = schema.tables["pa_goals"]
    rows = (await db.execute(select(goals).where(goals.c.user_id == user_id)
        .order_by(goals.c.updated_at.desc()))).mappings().all()
    pending = await draft(db, state, user_id)
    from ..plan_contract import missing_plan_fields
    missing = missing_plan_fields(pending) if pending and state["current_module"] == "module_2" else []
    return {"enabled": True, "runtime": dict(state), "goals": [dict(row) for row in rows],
            "draft": dict(pending) if pending else None, "record_hash": record_hash(pending) if pending else None,
            "can_confirm": bool(pending and not missing and state["last_transition_reason"] == "awaiting_record_confirmation"),
            "missing_fields": missing,
            "m1_reusable": (await initial_module(db, user_id=user_id)) == "module_2"}


@router.post("/{session_id}/goal")
async def select_goal(session_id: str, payload: Selection, user_id=Depends(require_subject_id),
                      db=Depends(get_db), store=Depends(get_session_store)):
    from ..graph.nodes import wait_for_pending_routing
    await wait_for_pending_routing(session_id)
    async with await store.get_turn_lock(session_id):
        conversation, state = await own_state(db, session_id, user_id)
        rt = schema.tables["conversation_runtime_states"]
        profiles = schema.tables["user_profile"]
        await db.execute(select(profiles.c.uuid).where(profiles.c.uuid == user_id).with_for_update())
        state = (await db.execute(select(rt).where(rt.c.conversation_id == conversation.id).with_for_update())).mappings().one()
        if state["row_version"] != payload.row_version:
            raise HTTPException(409, "进度已更新，请刷新后再操作")
        if await initial_module(db, user_id=user_id) != "module_2":
            raise HTTPException(409, "请先完成问题理解")
        if state["active_goal_id"]:
            raise HTTPException(409, "本聊天已经选择目标；讨论其他目标请新建聊天")
        if bool(payload.goal_id) == bool(payload.title):
            raise HTTPException(422, "请选择一个已有目标，或填写一个新目标")
        try:
            goal_id = payload.goal_id or await create_goal(db, user_id=user_id, title=payload.title,
                                                          conversation_id=conversation.id)
            goal = await owned_goal(db, user_id, goal_id, lock=True)
            if goal["status"] not in {"draft", "active"}:
                raise V2Conflict("请选择未结束的目标")
            cycles = schema.tables["pa_cycles"]
            cycle = (await db.execute(select(cycles).where(cycles.c.goal_id == goal_id,
                cycles.c.status.in_(["planning", "waiting_execution", "reviewing"])).order_by(cycles.c.ordinal.desc()).limit(1))).mappings().one_or_none()
            cycle_id = cycle["id"] if cycle else await start_cycle(db, user_id=user_id, goal_id=goal_id,
                                                                  conversation_id=conversation.id)
            module = "module_4" if cycle and cycle["status"] in {"waiting_execution", "reviewing"} else (
                "module_3" if cycle and cycle["module_two_record_id"] else "module_2")
            await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
                active_goal_id=goal_id, active_cycle_id=cycle_id, current_module=module, flow_status="active",
                memory={}, last_transition_reason="user_selected_goal", row_version=state["row_version"] + 1))
            conversation.revision += 1
            await db.commit()
            await store.reset(session_id)
        except (V2Conflict, V2NotFound) as exc:
            raise HTTPException(409, str(exc)) from None
    return await get_program(session_id, user_id, db)


@router.post("/{session_id}/confirm")
async def confirm_record(session_id: str, payload: Confirmation, user_id=Depends(require_subject_id),
                         db=Depends(get_db), store=Depends(get_session_store)):
    from ..graph.nodes import wait_for_pending_routing
    await wait_for_pending_routing(session_id)
    async with await store.get_turn_lock(session_id):
        conversation, state = await own_state(db, session_id, user_id)
        profiles, rt = schema.tables["user_profile"], schema.tables["conversation_runtime_states"]
        await db.execute(select(profiles.c.uuid).where(profiles.c.uuid == user_id).with_for_update())
        state = (await db.execute(select(rt).where(rt.c.conversation_id == conversation.id).with_for_update())).mappings().one()
        pending = await draft(db, state, user_id)
        if state["row_version"] != payload.row_version or not pending or pending["id"] != payload.record_id or record_hash(pending) != payload.record_hash:
            raise HTTPException(409, "记录已更新，请重新查看后确认")
        if state["last_transition_reason"] != "awaiting_record_confirmation":
            raise HTTPException(409, "当前模块的必要讨论尚未完成")
        module, cycle_id, goal_id = state["current_module"], state["active_cycle_id"], state["active_goal_id"]
        table = schema.tables[TABLES[module]]
        if module == "module_1" and not pending["functional_chain_summary"]:
            raise HTTPException(409, "问题理解摘要尚未保存，请继续讨论后刷新")
        if module == "module_3" and not pending["negotiated_record_plan"]:
            raise HTTPException(409, "记录办法尚未明确，请继续讨论")
        if module == "module_4" and (not pending["execution_result"] or not pending["review_decision"] or
                                     not (pending["phase_b"] or pending["review_summary"])):
            raise HTTPException(409, "执行反馈和下一步决定尚未完整，请继续讨论")
        # This is a real authenticated user action, visible in their transcript.
        visible_fields = {k: v for k, v in pending.items() if v is not None and k not in
            {"id", "user_id", "goal_id", "cycle_id", "created_at", "updated_at"}}
        position = int((await db.execute(select(func.max(ConversationMessage.position)).where(
            ConversationMessage.conversation_id == conversation.id))).scalar_one() or 0) + 1
        message = ConversationMessage(conversation_id=conversation.id, position=position, role="user",
            content={"module_1": "我已核对网页中的问题理解，确认符合我的情况，并愿意开始目标设定。",
                     "module_2": "我已核对网页中的活动计划，并确认按照这份计划尝试。",
                     "module_3": "我已核对网页中的记录方式，并同意按这个约定记录和反馈。",
                     "module_4": "我已核对网页中的复盘记录，并确认其中的下一步决定。"}[module])
        db.add(message)
        await db.flush()
        next_module, next_cycle, next_goal, flow = module, cycle_id, goal_id, "active"
        common = {"record_status": "confirmed", "confirmation_message_id": message.id, "updated_at": now()}
        try:
            if module == "module_1":
                await db.execute(update(table).where(table.c.id == pending["id"]).values(**common,
                    confirmation_status="confirmed", goal_setting_willingness="willing", willingness_message_id=message.id))
                m1 = schema.tables["user_module_one_state"]
                await db.execute(update(m1).where(m1.c.user_id == user_id).values(status="completed",
                    confirmed_formulation_id=pending["id"], completion_source="user_confirmed", evidence_status="available",
                    row_version=m1.c.row_version + 1, updated_at=now()))
                await db.execute(update(profiles).where(profiles.c.uuid == user_id).values(module1_done_flag=True, updated_at=now()))
                next_module = "module_2"
            elif module == "module_2":
                await confirm_plan(db, user_id=user_id, goal_id=goal_id, cycle_id=cycle_id,
                                   plan_id=pending["id"], message_id=message.id)
                next_module = "module_3"
            elif module == "module_3":
                await db.execute(update(table).where(table.c.id == pending["id"]).values(**common, acceptance_status="confirmed"))
                cycles = schema.tables["pa_cycles"]
                await db.execute(update(cycles).where(cycles.c.id == cycle_id, cycles.c.goal_id == goal_id).values(
                    module_three_record_id=pending["id"], status="waiting_execution"))
                next_module, flow = "module_4", "waiting_execution"
            else:
                await db.execute(update(table).where(table.c.id == pending["id"]).values(**common,
                    chain_confirmation_status="confirmed", confirmed_at=now()))
                cycles, goals = schema.tables["pa_cycles"], schema.tables["pa_goals"]
                await db.execute(update(cycles).where(cycles.c.id == cycle_id, cycles.c.goal_id == goal_id).values(
                    status="completed", completed_at=now()))
                decision = pending["review_decision"]
                if decision in (1, 3):
                    next_cycle = await start_cycle(db, user_id=user_id, goal_id=goal_id, conversation_id=conversation.id)
                    next_module = "module_2"
                elif decision == 2:
                    # Selection of a replacement is explicit; do not abandon other active goals.
                    await db.execute(update(goals).where(goals.c.id == goal_id).values(status="paused", status_reason="user_requested_replacement"))
                    next_goal, next_cycle, next_module = None, None, "module_2"
                else:
                    await db.execute(update(goals).where(goals.c.id == goal_id).values(status="completed", closed_at=now()))
                    flow = "completed"
                from ..models_business import InteractionStatus
                interaction = (await db.execute(select(InteractionStatus).where(InteractionStatus.user_id == user_id))).scalar_one_or_none()
                if not interaction:
                    interaction = InteractionStatus(user_id=user_id)
                    db.add(interaction)
                interaction.full_m2_m3_m4_cycle_count = int(interaction.full_m2_m3_m4_cycle_count or 0) + 1
                interaction.has_entered_closure_or_transition = True
            if cycle_id:
                # Other chats may be continuing this SAME cycle. They must not
                # keep stale module pointers or advance a completed cycle.
                from ..models import Conversation
                others = select(rt.c.conversation_id).join(Conversation, Conversation.id == rt.c.conversation_id).where(
                    rt.c.active_cycle_id == cycle_id, Conversation.subject_id == user_id,
                    rt.c.conversation_id != conversation.id)
                other_ids = list((await db.execute(others)).scalars())
                if other_ids:
                    await db.execute(update(rt).where(rt.c.conversation_id.in_(other_ids)).values(
                        current_module=next_module if module != "module_4" else "module_4",
                        flow_status=flow if module != "module_4" else "completed", memory={},
                        row_version=rt.c.row_version + 1, last_transition_reason="cycle_updated_in_other_chat"))
                    await db.execute(update(Conversation).where(Conversation.id.in_(other_ids)).values(revision=Conversation.revision + 1))
            await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
                current_module=next_module, active_goal_id=next_goal, active_cycle_id=next_cycle, flow_status=flow,
                memory={}, row_version=state["row_version"] + 1, last_transition_reason="user_confirmed_record"))
            await db.execute(insert(schema.tables["ai_decision_logs"]), {"conversation_id": conversation.id,
                "turn_id": str(message.id), "goal_id": goal_id, "cycle_id": cycle_id, "module_name": module,
                "decision_type": "user_confirmation", "decision_value": {"record_id": pending["id"],
                    "confirmed_snapshot_hash": payload.record_hash, "next_module": next_module},
                "evidence_message_ids": [message.id]})
            conversation.revision += 1
            await db.commit()
            await store.reset(session_id)
        except (V2Conflict, V2NotFound) as exc:
            await db.rollback()
            raise HTTPException(409, str(exc)) from None
    return await get_program(session_id, user_id, db)
