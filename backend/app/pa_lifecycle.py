"""PA cards are execution attempts; a reusable goal is not an attempt status."""
from sqlalchemy import select, update, insert
from .database_v2_schema import metadata as schema
from .v2_repository import now, V2Conflict

OPEN_CYCLES = ('planning', 'waiting_execution', 'reviewing')


def execution_outcome(review):
    """Never equate ending a review, or mood change, with doing the activity."""
    if not review or review.get('record_status') != 'confirmed':
        return None
    result = review.get('execution_result')
    return {1: 'completed', 2: 'not_started', 3: 'not_started', 4: 'partial'}.get(result)


async def read_pa_cards(db, *, user_id, goals):
    """Project one independently terminal card per cycle, preserving goal IDs."""
    cycles, plans, reviews = (schema.tables[n] for n in
                             ('pa_cycles', 'module_two_record', 'module_four_record'))
    owned = schema.tables['pa_goals']
    source = cycles.join(owned, owned.c.id == cycles.c.goal_id).outerjoin(plans,
        (plans.c.id == cycles.c.module_two_record_id) & (plans.c.goal_id == owned.c.id)
        & (plans.c.record_status == 'confirmed')).outerjoin(reviews, reviews.c.cycle_id == cycles.c.id)
    rows = (await db.execute(select(cycles.c.id, cycles.c.goal_id, cycles.c.ordinal,
        cycles.c.status, cycles.c.created_at, cycles.c.updated_at, cycles.c.completed_at,
        plans.c.id.label('plan_id'), plans.c.activity_content, plans.c.schedule_text,
        plans.c.location, plans.c.duration_minutes, reviews.c.record_status.label('review_status'),
        reviews.c.execution_result, reviews.c.scenario_type).select_from(source)
        .where(owned.c.user_id == user_id).order_by(cycles.c.created_at.desc(), cycles.c.id))).mappings().all()
    by_goal = {g['id']: g for g in goals}
    cards, seen = [], set()
    for row in rows:
        goal = by_goal.get(row['goal_id'])
        if goal is None:
            continue
        seen.add(goal['id'])
        ended = row['status'] in ('completed', 'cancelled')
        status = ('completed' if row['status'] == 'completed' else 'replaced'
                  if row['status'] == 'cancelled' and goal['status'] == 'replaced' else 'abandoned'
                  if row['status'] == 'cancelled' else 'paused' if goal['status'] == 'paused'
                  else 'draft' if row['status'] == 'planning' and not row['plan_id'] else 'active')
        cards.append({**goal, 'card_id': row['id'], 'goal_status': goal['status'],
            'status': status, 'created_at': row['created_at'], 'updated_at': row['updated_at'],
            'completed_at': row['completed_at'], 'cycle_status': row['status'],
            'latest_cycle': {'ordinal': row['ordinal'], 'status': row['status']},
            'execution_outcome': execution_outcome({'record_status': row['review_status'],
                'execution_result': row['execution_result']}) if ended else None,
            'review_scenario': row['scenario_type'] if ended and row['review_status'] == 'confirmed' else None,
            'plan': {k: row[k] for k in ('activity_content', 'schedule_text', 'location', 'duration_minutes')}
                    if row['plan_id'] else None})
    # Goals without a cycle still appear as drafts/legacy records, never vanish.
    return cards + [{**g, 'card_id': 'goal:' + g['id'], 'goal_status': g['status']}
                    for g in goals if g['id'] not in seen]


async def unfinished_core_goals(db, *, user_id):
    goals, cycles, plans, details = (schema.tables[n] for n in
        ('pa_goals', 'pa_cycles', 'module_two_record', 'pa_goal_details'))
    rows = (await db.execute(select(goals.c.id, goals.c.title, goals.c.row_version,
        plans.c.id.label('plan_id'), plans.c.activity_content, plans.c.schedule_text,
        plans.c.confirmation_message_id, cycles.c.id.label('cycle_id'), cycles.c.status.label('cycle_status'))
        .select_from(goals.join(details, details.c.goal_id == goals.c.id)
            .join(cycles, cycles.c.goal_id == goals.c.id).join(plans,
                (plans.c.id == cycles.c.module_two_record_id) & (plans.c.goal_id == goals.c.id)))
        .where(goals.c.user_id == user_id, goals.c.status == 'active', details.c.goal_kind == 'primary',
            cycles.c.status.in_(OPEN_CYCLES), plans.c.record_status == 'confirmed',
            plans.c.confirmation_status == 'confirmed').order_by(cycles.c.ordinal.desc()))).mappings().all()
    found = {}
    for row in rows:
        found.setdefault(row['id'], dict(row))
    return list(found.values())


CORE_CHOICE_POLICY = '''【未完成历史核心 PA 的处理】
以下记录仅包括已确认、尚未结束后续 M3/M4 的历史核心 PA。用户提出不同的新活动时，先中立提醒旧目标并澄清：替换原核心，还是保留原核心、把新活动作为额外次要活动？用户已经表达明确选择时直接承接，不重复询问。
用户不记得时先简要复述实际记录，再询问选择；不评判遗忘或未执行，不强迫继续旧目标。
选择替换：新活动作为新核心，进入 M2 阶段四目标具体化，旧核心归档失效；选择保留并新增：新活动只记录为次要活动，不进入完整核心目标流程，原核心不变。
只是犹豫、讨论或提出新活动不等于替换。M4 已结束的历史卡不属于这里的未完成目标。数据库操作是否成功以实际状态为准。'''


def core_choice(raw, candidates, messages):
    """Validate the model's semantic decision against an owned goal and source."""
    from .goal_contract import source_reference
    if not isinstance(raw, dict) or raw.get('action') not in ('keep', 'replace', 'additional'):
        return None
    goal = next((g for g in candidates if g['id'] == raw.get('goal_id')), None)
    source = source_reference(raw, messages)
    if not goal or source is None:
        return None
    # A historical choice cannot close a plan confirmed after that choice.
    if goal['confirmation_message_id'] is not None and source.id <= goal['confirmation_message_id']:
        return None
    boundary = next((m for m in messages if m.id == goal.get('after_message_id')), None)
    if goal.get('after_message_id') and (boundary is None or source.position <= boundary.position):
        return None
    return {'goal': goal, 'action': raw['action'], 'message_id': source.id, 'quote': raw['quote']}


async def replace_core_goal(db, *, user_id, old_goal_id, new_goal_id, conversation_id, choice):
    """Archive only the explicitly selected owned core, atomically with creation."""
    goals, cycles = schema.tables['pa_goals'], schema.tables['pa_cycles']
    old = (await db.execute(select(goals).where(goals.c.id == old_goal_id,
        goals.c.user_id == user_id).with_for_update())).mappings().one_or_none()
    new = await db.scalar(select(goals.c.id).where(goals.c.id == new_goal_id, goals.c.user_id == user_id))
    if not old or not new or old['status'] != 'active' or old_goal_id == new_goal_id:
        raise V2Conflict('原核心目标已变化，请重新读取当前状态')
    stamp = now()
    await db.execute(update(goals).where(goals.c.id == old_goal_id).values(status='replaced',
        replaced_by_goal_id=new_goal_id, status_reason='user_chose_new_core', closed_at=stamp,
        updated_at=stamp, row_version=old['row_version'] + 1))
    await db.execute(update(cycles).where(cycles.c.goal_id == old_goal_id,
        cycles.c.status.in_(OPEN_CYCLES)).values(status='cancelled', completed_at=stamp, updated_at=stamp))
    # Other chats must not keep editing a core explicitly replaced by its owner.
    from .models import Conversation
    from .routing_modes import effective_routing_mode, ROUTER_ONLY
    from types import SimpleNamespace
    rt = schema.tables['conversation_runtime_states']
    others = (await db.execute(select(rt).join(Conversation, Conversation.id == rt.c.conversation_id)
        .where(rt.c.active_goal_id == old_goal_id, Conversation.subject_id == user_id,
            rt.c.conversation_id != conversation_id).with_for_update())).mappings().all()
    for state in others:
        owner = SimpleNamespace(id=state['conversation_id'], subject_id=user_id)
        router_only = await effective_routing_mode(db, conversation=owner, state=state, user_id=user_id) == ROUTER_ONLY
        memory = {k:v for k,v in (state['memory'] or {}).items()
            if k not in {'last_reviewed_cycle', 'dialogue_draft', 'module_extraction_freshness', 'pa_card'}}
        await db.execute(update(rt).where(rt.c.conversation_id == state['conversation_id']).values(
            active_goal_id=None, active_cycle_id=None, flow_status='active', memory=memory,
            current_module=state['current_module'] if router_only else 'module_2',
            row_version=rt.c.row_version + 1, last_transition_reason='core_replaced_in_other_chat'))
        await db.execute(update(Conversation).where(Conversation.id == state['conversation_id']).values(
            revision=Conversation.revision + 1))
    await db.execute(insert(schema.tables['ai_decision_logs']), {'conversation_id': conversation_id,
        'goal_id': old_goal_id, 'module_name': 'module_2', 'decision_type': 'core_goal_replaced',
        'turn_id': str(choice['message_id']), 'decision_value': {'new_goal_id': new_goal_id,
            'action': 'replace', 'source_quote': choice['quote']}, 'evidence_message_ids': [choice['message_id']]})


async def reviewed_goal(db, *, user_id, memory):
    marker = (memory or {}).get('last_reviewed_cycle')
    marker = marker if isinstance(marker, dict) else {}
    goals, cycles, plans = (schema.tables[n] for n in ('pa_goals', 'pa_cycles', 'module_two_record'))
    row = (await db.execute(select(goals.c.id, goals.c.title, goals.c.row_version,
        cycles.c.id.label('cycle_id'), cycles.c.status.label('cycle_status'),
        plans.c.id.label('plan_id'), plans.c.activity_content, plans.c.schedule_text,
        plans.c.confirmation_message_id).select_from(goals.join(cycles, cycles.c.goal_id == goals.c.id)
            .join(plans, (plans.c.id == cycles.c.module_two_record_id) & (plans.c.goal_id == goals.c.id)))
        .where(goals.c.user_id == user_id, goals.c.id == marker.get('goal_id'),
            cycles.c.id == marker.get('cycle_id'), cycles.c.status == 'completed',
            goals.c.status == 'active', plans.c.record_status == 'confirmed'))).mappings().one_or_none()
    return {**dict(row), 'after_message_id': marker.get('after_message_id')} if row else None


async def keep_reviewed_goal(db, *, user_id, conversation_id, goal, values):
    """An explicit M2 choice starts a draft; the old completed card never reopens."""
    from .v2_repository import start_cycle, append_plan_draft, PLAN_WRITABLE_FIELDS
    plans, cycles = schema.tables['module_two_record'], schema.tables['pa_cycles']
    baseline = (await db.execute(select(plans).where(plans.c.id == goal['plan_id'],
        plans.c.goal_id == goal['id'], plans.c.record_status == 'confirmed'))).mappings().one()
    fields = {k: baseline[k] for k in PLAN_WRITABLE_FIELDS}
    # An old absolute date must not silently schedule a new attempt in the past.
    fields['scheduled_start_at'] = None
    fields.update({k: v for k, v in values.items() if v is not None})
    cycle_id = await start_cycle(db, user_id=user_id, goal_id=goal['id'], conversation_id=conversation_id)
    plan_id = await append_plan_draft(db, user_id=user_id, goal_id=goal['id'], fields=fields)
    details = schema.tables['pa_plan_details']
    context = (await db.execute(select(details).where(details.c.plan_id == baseline['id']))).mappings().one_or_none()
    if context:
        await db.execute(insert(details), {'plan_id': plan_id,
            **{k:context[k] for k in ('schedule_kind', 'review_cadence', 'difficulty', 'resources')}})
    await db.execute(update(cycles).where(cycles.c.id == cycle_id).values(module_two_record_id=plan_id))
    return {'goal_id': goal['id'], 'cycle_id': cycle_id, 'plan_id': plan_id}


M4_CLOSURE_POLICY = '''本轮 PA 生命周期：M4 确定本轮执行结果并完成相应复盘、自然总结后，结束的是本轮尝试。是否保留、调整或替换目标由下一轮 M2 与用户讨论决定；M4 不要求用户先做该决定，不自动进入下一轮 M3。执行前的犹豫、暂时不想做仍在本轮处理；最终明确本轮不执行或执行窗口结束，才按未执行收尾。'''
M2_REVIEW_POLICY = '''上一轮 PA 已结束：本轮 M2 先承接复盘，和用户讨论保留原目标还是选择新目标。已经明确表达的选择不重复询问。保留可沿用已有内容完善新一轮计划，旧卡保持结束；替换只有用户明确选择后才归档旧核心并围绕新核心具体化。不能把进入 M2 当作用户已经选择替换。'''
