"""Authenticated V2 programme selection and explicit record confirmation."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update, func
from ..db import get_db
from ..identity import require_subject_id
from ..database_v2_schema import metadata as schema
from ..models import ConversationMessage
from ..session import get_session_store
from ..v2_profile import enabled
from ..v2_repository import start_cycle, owned_goal, initial_module, V2Conflict, V2NotFound
from ..v2_workflow import runtime_for, module_extraction_is_current
from ..program_confirmation import draft, current_m1_missing, record_hash, validate_confirmation, commit_confirmation

router = APIRouter(prefix="/program", tags=["program"])


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_id: str = Field(min_length=1, max_length=36)
    row_version: int = Field(ge=0)
    resume: bool = False


class Confirmation(BaseModel):
    record_id: str = Field(min_length=1, max_length=36)
    record_hash: str = Field(min_length=64, max_length=64)
    row_version: int = Field(ge=0)


async def own_state(db, session_id, user_id):
    if not enabled():
        raise HTTPException(404, "V2 尚未启用")
    conversation, state = await runtime_for(db, session_id)
    if not conversation or conversation.subject_id != user_id or not state:
        raise HTTPException(404, "聊天不存在")
    if state["memory"].get("sandbox_mode") == "true":
        raise HTTPException(409, "测试沙盒不写入正式目标")
    return conversation, state


@router.get("/goals/overview")
async def get_goal_overview(user_id=Depends(require_subject_id), db=Depends(get_db)):
    """User-owned overview independent of the currently open conversation.

    Only expose the confirmed current plan, never an unconfirmed extraction.
    Joins keep this to one list query even when a user has many goals/cycles.
    """
    if not enabled():
        return {"enabled": False, "m1_reusable": False, "goals": []}
    goals, plans, cycles = (schema.tables[name] for name in
                            ("pa_goals", "module_two_record", "pa_cycles"))
    latest = select(cycles.c.goal_id, func.max(cycles.c.ordinal).label("ordinal")).join(
        goals, goals.c.id == cycles.c.goal_id).where(goals.c.user_id == user_id).group_by(cycles.c.goal_id).subquery()
    source = goals.outerjoin(plans, (plans.c.id == goals.c.current_plan_record_id) &
        (plans.c.goal_id == goals.c.id) & (plans.c.record_status == "confirmed")).outerjoin(
        latest, latest.c.goal_id == goals.c.id).outerjoin(cycles,
        (cycles.c.goal_id == goals.c.id) & (cycles.c.ordinal == latest.c.ordinal))
    rows = (await db.execute(select(goals.c.id, goals.c.title, goals.c.status,
        goals.c.created_at, goals.c.updated_at, plans.c.id.label("plan_id"),
        plans.c.activity_content, plans.c.schedule_text, plans.c.location, plans.c.duration_minutes,
        cycles.c.ordinal, cycles.c.status.label("cycle_status")).select_from(source)
        .where(goals.c.user_id == user_id).order_by(goals.c.updated_at.desc(), goals.c.id))).mappings().all()
    from ..goal_contract import public_goal_details, public_activities
    details = await public_goal_details(db, user_id)
    plan_details = schema.tables["pa_plan_details"]
    plan_ids = [row["plan_id"] for row in rows if row["plan_id"]]
    plan_context = {r["plan_id"]: {k: r[k] for k in ("schedule_kind", "review_cadence")} for r in
        (await db.execute(select(plan_details).where(plan_details.c.plan_id.in_(plan_ids)))).mappings()}
    return {"enabled": True, "m1_reusable": (await initial_module(db, user_id=user_id)) == "module_2",
        "activity_records": await public_activities(db, user_id),
        "goals": [{**{key: row[key] for key in ("id", "title", "status", "created_at", "updated_at")},
            **details.get(row["id"], {"goal_kind": "unclassified", "long_term_direction": None}),
            "plan": {**{key: row[key] for key in ("activity_content", "schedule_text", "location", "duration_minutes")},
                     **plan_context.get(row["plan_id"], {"schedule_kind": "unspecified", "review_cadence": None})}
                    if row["plan_id"] else None,
            "latest_cycle": {"ordinal": row["ordinal"], "status": row["cycle_status"]}
                            if row["ordinal"] is not None else None} for row in rows]}


@router.get("/goals/{goal_id}/history")
async def get_goal_history(goal_id: str, page: int = Query(1, ge=1, le=100000),
                           page_size: int = Query(20, ge=1, le=50),
                           user_id=Depends(require_subject_id), db=Depends(get_db)):
    if not enabled():
        raise HTTPException(404, "目标历史尚未启用")
    from ..goal_history import read_goal_history
    result = await read_goal_history(db, user_id=user_id, goal_id=goal_id, page=page, page_size=page_size)
    if result is None:
        raise HTTPException(404, "目标不存在")
    return result


@router.get("/{session_id}")
async def get_program(session_id: str, user_id=Depends(require_subject_id), db=Depends(get_db)):
    if not enabled():
        return {"enabled": False}
    conversation, state = await own_state(db, session_id, user_id)
    goals = schema.tables["pa_goals"]
    rows = (await db.execute(select(goals).where(goals.c.user_id == user_id)
        .order_by(goals.c.updated_at.desc()))).mappings().all()
    pending = await draft(db, state, user_id)
    review_action = None
    if pending and state["current_module"] == "module_4":
        details_table = schema.tables["pa_review_details"]
        review_action = (await db.execute(select(details_table.c.action).where(details_table.c.review_id == pending["id"]))).scalar_one_or_none()
    missing, readiness = [], None
    # A paused/completed runtime has no open confirmation window.  Do not
    # evaluate its historical draft as if it were the current active module;
    # doing so exposed stale extraction/readiness errors after a successful
    # M4 transition even though the cycle and goal were already closed.
    if (state["current_module"] in {"module_2", "module_3", "module_4"}
            and state.get("flow_status") not in {"paused", "completed"}):
        from ..program_confirmation import confirmation_readiness
        readiness = await confirmation_readiness(db, conversation=conversation,
            state=state, user_id=user_id, session_id=session_id, pending=pending)
        missing = list(readiness["missing_fields"])
        if not readiness["extraction_fresh"]:
            missing.append("extraction_refresh")
        if pending and state["current_module"] == "module_4" and not review_action:
            missing.append("review_followup")
    m1_contract = None
    if state["current_module"] == "module_1":
        from ..m1_contract import missing_m1_fields, contract_for
        missing = await current_m1_missing(db, pending, conversation.id, session_id)
        contract = contract_for(pending)
        m1_contract = {k: contract.get(k) for k in ("version", "path", "milestones", "user_approval_level")}
    public_draft = dict(pending) if pending else None
    if public_draft and isinstance(public_draft.get("event_experience"), dict):
        public_draft["event_experience"] = {k: v for k, v in public_draft["event_experience"].items() if k != "_m1_contract"}
    if public_draft and isinstance(public_draft.get("phase_c"), dict):
        public_draft["phase_c"] = {k: v for k, v in public_draft["phase_c"].items() if k != "_m4_contract"}
    from ..goal_contract import public_goal_details, public_activities
    details = await public_goal_details(db, user_id)
    return {"enabled": True, "runtime": dict(state), "goals": [{**dict(row), **details.get(row["id"],
                {"goal_kind": "unclassified", "long_term_direction": None})} for row in rows],
            "goal_context": details.get(state["active_goal_id"], {"goal_kind": "unclassified", "long_term_direction": None}) if state["active_goal_id"] else None,
            "review_action": review_action,
            "activity_records": await public_activities(db, user_id, conversation_id=conversation.id),
            "draft": public_draft, "record_hash": record_hash(pending) if pending else None,
            "m1_contract": m1_contract,
            "can_confirm": bool(pending and not missing and (readiness is None or readiness["ready"])
                                and state["last_transition_reason"] == "awaiting_record_confirmation"),
            "missing_fields": missing,
            "readiness": {k: readiness[k] for k in ("ready", "module", "missing_fields", "reasons")} if readiness else None,
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
        try:
            goal_id = payload.goal_id
            goal = await owned_goal(db, user_id, goal_id, lock=True)
            if goal["status"] == "paused" and payload.resume:
                from ..v2_repository import resume_paused_goal
                await resume_paused_goal(db, user_id=user_id, goal_id=goal_id, conversation_id=conversation.id)
                goal = await owned_goal(db, user_id, goal_id)
            if goal["status"] not in {"draft", "active"}:
                raise V2Conflict("请选择未结束的目标")
            cycles = schema.tables["pa_cycles"]
            cycle = (await db.execute(select(cycles).where(cycles.c.goal_id == goal_id,
                cycles.c.status.in_(["planning", "waiting_execution", "reviewing"])).order_by(cycles.c.ordinal.desc()).limit(1))).mappings().one_or_none()
            if not cycle and goal["status"] == "active" and goal["current_plan_record_id"]:
                from ..v2_repository import resume_paused_goal
                resumed = await resume_paused_goal(db, user_id=user_id, goal_id=goal_id,
                    conversation_id=conversation.id, retained=True)
                cycle = (await db.execute(select(cycles).where(cycles.c.id == resumed))).mappings().one()
            cycle_id = cycle["id"] if cycle else await start_cycle(db, user_id=user_id, goal_id=goal_id,
                                                                  conversation_id=conversation.id)
            module = "module_4" if cycle and cycle["status"] in {"waiting_execution", "reviewing"} else (
                "module_3" if cycle and cycle["module_two_record_id"] else "module_2")
            await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
                active_goal_id=goal_id, active_cycle_id=cycle_id, current_module=module,
                flow_status="waiting_execution" if cycle and cycle["status"] == "waiting_execution" else "active",
                memory={}, last_transition_reason="user_selected_goal", row_version=state["row_version"] + 1))
            conversation.revision += 1
            await db.commit()
            await store.reset(session_id)
        except (V2Conflict, V2NotFound) as exc:
            await db.rollback()
            raise HTTPException(409, str(exc)) from None
    return await get_program(session_id, user_id, db)


@router.post("/{session_id}/confirm", deprecated=True)
async def confirm_record(session_id: str, payload: Confirmation, user_id=Depends(require_subject_id),
                         db=Depends(get_db), store=Depends(get_session_store)):
    """Legacy-client compatibility only; the current UI has no confirmation action.

    New dialogue commits call the shared transaction service with an existing
    user message, never this adapter or its legacy button-action transcript.
    """
    from ..graph.nodes import wait_for_pending_routing
    await wait_for_pending_routing(session_id)
    async with await store.get_turn_lock(session_id):
        conversation, state = await own_state(db, session_id, user_id)
        profiles, rt = schema.tables["user_profile"], schema.tables["conversation_runtime_states"]
        await db.execute(select(profiles.c.uuid).where(profiles.c.uuid == user_id).with_for_update())
        state = (await db.execute(select(rt).where(rt.c.conversation_id == conversation.id).with_for_update())).mappings().one()
        pending, review_action = await validate_confirmation(db, conversation=conversation, state=state,
            user_id=user_id, session_id=session_id, payload=payload)
        module = state["current_module"]
        # This is a real authenticated user action, visible in their transcript.
        position = int((await db.execute(select(func.max(ConversationMessage.position)).where(
            ConversationMessage.conversation_id == conversation.id))).scalar_one() or 0) + 1
        message = ConversationMessage(conversation_id=conversation.id, position=position, role="user",
            content={"module_1": "我已核对网页中的记录，理解 BA 的基本方法，并愿意开始目标设定。",
                     "module_2": "我已核对网页中的活动计划，并确认按照这份计划尝试。",
                     "module_3": "我已核对网页中的记录方式，并同意按这个约定记录和反馈。",
                     "module_4": "我已核对网页中的复盘记录，并确认其中的下一步决定。"}[module])
        db.add(message)
        await db.flush()
        try:
            await commit_confirmation(db, conversation=conversation, state=state, user_id=user_id,
                pending=pending, message=message, review_action=review_action,
                snapshot_hash=payload.record_hash, source="legacy_authenticated_confirmation")
            await db.commit()
            await store.reset(session_id)
        except (V2Conflict, V2NotFound) as exc:
            await db.rollback()
            raise HTTPException(409, str(exc)) from None
    return await get_program(session_id, user_id, db)
