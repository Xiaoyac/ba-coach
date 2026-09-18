"""Source-scoped deletion. Caller owns the transaction and session lock."""
from sqlalchemy import select, update, delete, or_
from .database_v2_schema import metadata as schema
from .models import Conversation, ConversationMessage, AIExecutionEvent, ConversationModuleProgress


async def detach_conversation(db, conversation):
    cid, uid = conversation.id, conversation.subject_id
    message_ids = list((await db.execute(select(ConversationMessage.id).where(
        ConversationMessage.conversation_id == cid))).scalars())
    goals, cycles, rt = [schema.tables[n] for n in (
        "pa_goals", "pa_cycles", "conversation_runtime_states")]
    logs = schema.tables["ai_decision_logs"]
    candidates = list((await db.execute(select(goals.c.id).where(
        goals.c.user_id == uid, goals.c.created_from_conversation_id == cid
    ).with_for_update())).scalars())
    shared = set()
    if candidates:
        for query in (
            select(rt.c.active_goal_id).where(rt.c.active_goal_id.in_(candidates), rt.c.conversation_id != cid),
            select(cycles.c.goal_id).join(rt, rt.c.active_cycle_id == cycles.c.id).where(
                cycles.c.goal_id.in_(candidates), rt.c.conversation_id != cid),
            select(cycles.c.goal_id).where(cycles.c.goal_id.in_(candidates),
                or_(cycles.c.started_from_conversation_id != cid, cycles.c.started_from_conversation_id.is_(None))),
            select(logs.c.goal_id).where(logs.c.goal_id.in_(candidates), logs.c.conversation_id != cid),
            select(goals.c.replaced_by_goal_id).where(goals.c.replaced_by_goal_id.in_(candidates), ~goals.c.id.in_(candidates)),
        ):
            shared.update((await db.execute(query)).scalars())
        other_messages = select(ConversationMessage.id).join(Conversation).where(
            Conversation.subject_id == uid, Conversation.id != cid)
        for name in ("module_two_record", "module_three_record"):
            table = schema.tables[name]
            shared.update((await db.execute(select(table.c.goal_id).where(
                table.c.goal_id.in_(candidates), table.c.confirmation_message_id.in_(other_messages)))).scalars())
        # A retained replacement chain cannot point at a deleted successor.
        while shared:
            referenced = set((await db.execute(select(goals.c.replaced_by_goal_id).where(
                goals.c.id.in_(shared), goals.c.replaced_by_goal_id.in_(candidates)))).scalars())
            if referenced <= shared:
                break
            shared.update(referenced)
    exclusive = set(candidates) - shared
    activities = schema.tables["pa_activity_events"]
    await db.execute(delete(activities).where(activities.c.user_id == uid,
        activities.c.source_conversation_id == cid))
    details = schema.tables["pa_goal_details"]
    # Erase deleted-chat evidence, even when a shared goal itself must remain.
    await db.execute(delete(details).where(details.c.source_conversation_id == cid))
    review_details = schema.tables["pa_review_details"]
    await db.execute(delete(review_details).where(review_details.c.source_message_id.in_(message_ids)))
    # Shared goals and their full plan/cycle history remain usable elsewhere.
    await db.execute(update(goals).where(goals.c.id.in_(shared)).values(created_from_conversation_id=None))
    await db.execute(delete(rt).where(rt.c.conversation_id == cid))
    await db.execute(delete(logs).where(logs.c.conversation_id == cid))
    if exclusive:
        await db.execute(delete(activities).where(activities.c.goal_id.in_(exclusive)))
        await db.execute(delete(details).where(details.c.goal_id.in_(exclusive)))
        plan_details = schema.tables["pa_plan_details"]
        plan_ids = select(schema.tables["module_two_record"].c.id).where(schema.tables["module_two_record"].c.goal_id.in_(exclusive))
        await db.execute(delete(plan_details).where(plan_details.c.plan_id.in_(plan_ids)))
        cycle_ids = list((await db.execute(select(cycles.c.id).where(cycles.c.goal_id.in_(exclusive)))).scalars())
        review_ids = select(schema.tables["module_four_record"].c.id).where(schema.tables["module_four_record"].c.cycle_id.in_(cycle_ids))
        await db.execute(delete(review_details).where(review_details.c.review_id.in_(review_ids)))
        await db.execute(update(logs).where(logs.c.goal_id.in_(exclusive)).values(goal_id=None))
        await db.execute(update(logs).where(logs.c.cycle_id.in_(cycle_ids)).values(cycle_id=None))
        for name in ("module_four_record", "pa_cycle_progress"):
            table = schema.tables[name]
            await db.execute(delete(table).where(table.c.cycle_id.in_(cycle_ids)))
        await db.execute(delete(cycles).where(cycles.c.id.in_(cycle_ids)))
        for name in ("module_three_record", "module_two_record"):
            table = schema.tables[name]
            await db.execute(delete(table).where(table.c.goal_id.in_(exclusive)))
        await db.execute(update(goals).where(goals.c.id.in_(exclusive)).values(replaced_by_goal_id=None, current_plan_record_id=None))
        await db.execute(delete(goals).where(goals.c.id.in_(exclusive), goals.c.user_id == uid))
    await db.execute(update(cycles).where(cycles.c.started_from_conversation_id == cid).values(started_from_conversation_id=None))
    # Do not revive older superseded memories when their replacement is erased.
    for name in ("ba_memory", "user_activity_constraints"):
        table = schema.tables[name]
        removed = list((await db.execute(select(table.c.id).where(
            table.c.user_id == uid, table.c.source_message_id.in_(message_ids)))).scalars())
        await db.execute(update(table).where(table.c.supersedes_id.in_(removed)).values(supersedes_id=None))
        await db.execute(delete(table).where(table.c.user_id == uid, table.c.id.in_(removed)))
    one, state = schema.tables["module_one_record"], schema.tables["user_module_one_state"]
    formulations = list((await db.execute(select(one.c.id).where(one.c.user_id == uid,
        or_(one.c.confirmation_message_id.in_(message_ids), one.c.ba_explanation_message_id.in_(message_ids),
            one.c.willingness_message_id.in_(message_ids)),
        ~one.c.id.in_(select(goals.c.module_one_record_id).where(goals.c.module_one_record_id.is_not(None)))))).scalars())
    await db.execute(update(state).where(state.c.user_id == uid, state.c.confirmed_formulation_id.in_(formulations))
        .values(confirmed_formulation_id=None, completed_steps=[], status="in_progress", completion_source="none",
                evidence_status="missing", row_version=state.c.row_version + 1))
    await db.execute(delete(one).where(one.c.user_id == uid, one.c.id.in_(formulations)))
    await db.execute(delete(AIExecutionEvent).where(AIExecutionEvent.conversation_id == cid))
    await db.execute(delete(ConversationModuleProgress).where(ConversationModuleProgress.conversation_id == cid))
    await db.execute(delete(ConversationMessage).where(ConversationMessage.conversation_id == cid))
    await db.execute(delete(Conversation).where(Conversation.id == cid, Conversation.subject_id == uid))
