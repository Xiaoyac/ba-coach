"""Source-backed activity identity, separate from goals and review cycles.

Models propose a relation using owned candidates. Code checks references, time
anchors and relational integrity; it never picks a goal by its display name.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, insert, update, delete

from .database_v2_schema import metadata as schema
from .v2_repository import new_id, now


ATTRIBUTION_SCHEMA = '''每项还可含 attribution 对象：
status=linked（有证据关联已有目标）/unplanned（明确是未设目标活动）/uncertain（无法确定）；
goal_id、plan_id、cycle_id只能使用activity_candidates中的真实ID，未知null，不能按活动名称相同直接选当前目标。
relation=new（这次新发生）/existing（补充已记录的同一次执行）；execution_id仅existing时填候选中的真实执行ID。
无明确日期但上下文明指本轮已确认安排，可带cycle_reference={message_id,quote}引用该计划的真实confirmation_source；后台核验当前执行绑定，不能以裸布尔值代替依据。同名历史可能混淆时，还必须提供disambiguation_ref={message_id,quote}引用用户明确区分新旧/本轮安排的原话；短“做完了”或“不记得哪次”不能作为消歧证据。
evidence={message_id,quote}是支持本次归属/同一次关系的真实用户连续原话；允许结合此前明确上下文作语义判断，不伪造ID。
未给日期时保留未知，不凭发送日期认定发生于今天；occurred_at_text保留本条原话。相对日期以该条消息发送时间为锚。
归属目标与归属复盘周期分开：明确旧目标的新一次执行可关联旧goal/plan而cycle_id=null，不能重开已结束复盘；只有实际发生时间/同次证据支持才关联cycle。
同名新旧目标、未明确哪一次、时间不清楚时status=uncertain；待澄清不等于没发生。又做一次必须relation=new，不能覆盖前一次。
补充旧执行用existing，已结束周期仅追加事实，不能修改旧复盘或当前核心复盘。
'''


def _day(value):
    if not isinstance(value, datetime):
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(ZoneInfo('Asia/Shanghai')).date()


def occurrence_day(text, source):
    """Interpret only an exact date expression from this authenticated message.

    This is date parsing, not goal/occasion classification. Ambiguous dates
    stay textual, and re-extraction never uses its current wall clock.
    """
    if not isinstance(text, str) or not text or text not in source.content:
        return None
    relative = {'今天': 0, '昨天': -1, '前天': -2, 'today': 0, 'yesterday': -1}
    cleaned = text.strip().lower()
    # Only explicit, single-day expressions; no month/day rounding, weekday
    # guesses, or fabricated times. Qualifiers/ranges do not match this grammar.
    day_word = re.fullmatch(r'(今天|昨天|前天)(?:早上|早晨|上午|中午|下午|傍晚|晚上|夜里)?', cleaned)
    if day_word:
        cleaned = day_word.group(1)
    if cleaned in {'刚才', '刚刚', '刚刚那次'}:
        cleaned = '今天'
    if cleaned in relative:
        anchor = _day(getattr(source, 'created_at', None))
        return anchor + timedelta(days=relative[cleaned]) if anchor else None
    match = re.fullmatch(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?', cleaned)
    if match:
        try:
            return date(*map(int, match.groups()))
        except ValueError:
            pass
    return None


async def activity_candidates(db, user_id, *, conversation_id=None, goal_id=None, activity_query=None, source_text=None, offset=0, history_offset=0):
    goals, plans, cycles, executions, events, links = (schema.tables[name] for name in (
        'pa_goals', 'module_two_record', 'pa_cycles', 'pa_executions', 'pa_activity_events', 'pa_activity_attributions'))
    from .goal_scope import goal_filter, isolated
    owned_filter = await goal_filter(db, user_id, conversation_id)
    test_isolated = await isolated(db, user_id)
    goal_query = select(goals.c.id, goals.c.title, goals.c.status,
        goals.c.created_at, goals.c.closed_at).where(owned_filter)
    if goal_id:
        goal_query = goal_query.where(goals.c.id == goal_id)
    if activity_query:
        # Search merely supplies candidates. It cannot assign an execution.
        escaped = activity_query[:120].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        matching_plans = select(plans.c.goal_id).where(plans.c.activity_content.like('%' + escaped + '%', escape='\\'))
        from sqlalchemy import or_
        goal_query = goal_query.where(or_(goals.c.title.like('%' + escaped + '%', escape='\\'), goals.c.id.in_(matching_plans)))
    all_goals = (await db.execute(goal_query.order_by(goals.c.created_at.desc(), goals.c.id))).mappings().all()
    # Latest candidates plus old goals literally mentioned in this input. This
    # is retrieval only, never a name-based assignment. Older pages stay queryable.
    if source_text and not goal_id and not activity_query:
        mentioned = [g for g in all_goals if len(g['title']) > 1 and g['title'] in source_text]
        rest = [g for g in all_goals if g not in mentioned]
        all_goals = mentioned + rest
    offset = max(0, int(offset))
    history_offset = max(0, int(history_offset))
    owned_goals = list(all_goals[offset:offset + 20])
    # Current scope is always supplied, even when its goal is older than the
    # history page. This is a persisted binding, not semantic name matching.
    runtime = schema.tables['conversation_runtime_states']
    current = (await db.execute(select(runtime).where(
        runtime.c.conversation_id == conversation_id))).mappings().one_or_none() if conversation_id is not None else None
    current_goal = next((g for g in all_goals if current and g['id'] == current['active_goal_id']), None)
    if current_goal and current_goal not in owned_goals:
        owned_goals.append(current_goal)
    selected_ids = [g['id'] for g in owned_goals]
    owned_plans = (await db.execute(select(plans.c.id, plans.c.goal_id, plans.c.version_no,
        plans.c.activity_content, plans.c.schedule_text, plans.c.scheduled_start_at,
        plans.c.timezone, plans.c.confirmation_message_id, plans.c.created_at)
        .join(goals, goals.c.id == plans.c.goal_id).where(goals.c.user_id == user_id,
            plans.c.confirmation_status == 'confirmed', plans.c.confirmation_message_id.is_not(None),
            goals.c.id.in_(selected_ids))
        .order_by(plans.c.created_at.desc(), plans.c.id).offset(history_offset).limit(80))).mappings().all()
    owned_cycles = (await db.execute(select(cycles.c.id, cycles.c.goal_id,
        cycles.c.module_two_record_id.label('plan_id'), cycles.c.status, cycles.c.ordinal,
        cycles.c.planned_for_at, cycles.c.created_at, cycles.c.completed_at)
        .join(goals, goals.c.id == cycles.c.goal_id).where(goals.c.user_id == user_id, goals.c.id.in_(selected_ids))
        .order_by(cycles.c.created_at.desc(), cycles.c.id).offset(history_offset).limit(80))).mappings().all()
    if current_goal and current['active_cycle_id']:
        current_cycle = (await db.execute(select(cycles.c.id, cycles.c.goal_id,
            cycles.c.module_two_record_id.label('plan_id'), cycles.c.status, cycles.c.ordinal,
            cycles.c.planned_for_at, cycles.c.created_at, cycles.c.completed_at).where(
                cycles.c.id == current['active_cycle_id'], cycles.c.goal_id == current_goal['id']))).mappings().one_or_none()
        if current_cycle:
            if not any(c['id'] == current_cycle['id'] for c in owned_cycles):
                owned_cycles = list(owned_cycles) + [current_cycle]
            if not any(p['id'] == current_cycle['plan_id'] for p in owned_plans):
                current_plan = (await db.execute(select(plans.c.id, plans.c.goal_id, plans.c.version_no,
                    plans.c.activity_content, plans.c.schedule_text, plans.c.scheduled_start_at,
                    plans.c.timezone, plans.c.confirmation_message_id, plans.c.created_at).where(
                        plans.c.id == current_cycle['plan_id'], plans.c.goal_id == current_goal['id'],
                        plans.c.confirmation_status == 'confirmed', plans.c.confirmation_message_id.is_not(None)))).mappings().one_or_none()
                if current_plan:
                    owned_plans = list(owned_plans) + [current_plan]
    active_execution = select(links.c.execution_id).join(events, events.c.id == links.c.event_id).where(
        events.c.status == 'active', events.c.user_id == user_id)
    owned_exec = (await db.execute(select(executions).where(executions.c.id.in_(active_execution), executions.c.user_id == user_id,
        executions.c.goal_id.in_(selected_ids) if (goal_id or activity_query) else True,
        (executions.c.source_conversation_id == conversation_id if conversation_id is not None else False) if test_isolated else True)
        .order_by(executions.c.created_at.desc(), executions.c.id).offset(history_offset).limit(80))).mappings().all()
    if current_goal and current['active_cycle_id']:
        current_executions = (await db.execute(select(executions).where(
            executions.c.user_id == user_id, executions.c.cycle_id == current['active_cycle_id'],
            executions.c.goal_id == current_goal['id'], executions.c.id.in_(active_execution),
            executions.c.source_conversation_id == conversation_id if test_isolated else True)
            .order_by(executions.c.created_at.desc(), executions.c.id).limit(8))).mappings().all()
        owned_exec = list(owned_exec) + [e for e in current_executions if not any(x['id'] == e['id'] for x in owned_exec)]
    observations = (await db.execute(select(events.c.id, events.c.source_message_id,
        events.c.activity_content, events.c.source_quote, events.c.occurred_at_text,
        links.c.execution_id, links.c.attribution_status, links.c.reason_code)
        .join(links, links.c.event_id == events.c.id).where(events.c.user_id == user_id,
            events.c.status == 'active',
            (events.c.source_conversation_id == conversation_id if conversation_id is not None else False) if test_isolated else True, links.c.execution_id.in_([e['id'] for e in owned_exec]) | (links.c.execution_id.is_(None)))
        .order_by(events.c.created_at.desc(), events.c.id).offset(history_offset).limit(80))).mappings().all()
    from .models import ConversationMessage, Conversation
    confirmation_ids = [p['confirmation_message_id'] for p in owned_plans]
    sources = (await db.execute(select(ConversationMessage.id, ConversationMessage.content,
        ConversationMessage.created_at).join(Conversation, Conversation.id == ConversationMessage.conversation_id)
        .where(Conversation.subject_id == user_id, ConversationMessage.role == 'user',
            ConversationMessage.id.in_(confirmation_ids),
            Conversation.id == conversation_id if test_isolated else True))).mappings().all()
    return json.loads(json.dumps({'goals': [dict(r) for r in owned_goals],
        'confirmation_sources': [dict(r) for r in sources],
        'page': {'offset': offset, 'goal_total': len(all_goals), 'next_offset': offset + 20 if len(all_goals) > offset + 20 else None,
            'next_history_offset': history_offset + 80 if max(len(owned_plans),len(owned_cycles),len(owned_exec),len(observations)) >= 80 else None,
            'history_may_be_omitted': len(all_goals) > len(owned_goals) or len(owned_plans) >= 80 or len(owned_cycles) >= 80 or len(owned_exec) >= 80 or len(observations) >= 80},
        'plans': [dict(r) for r in owned_plans], 'cycles': [dict(r) for r in owned_cycles],
        'executions': [dict(r) for r in owned_exec], 'observations': [dict(r) for r in observations]}, default=str, ensure_ascii=False))


async def _resolve(db, *, user_id, item, source, messages):
    from .goal_contract import source_reference
    proposal = item.get('attribution')
    if item['event_kind'] == 'idea':
        return {'status': 'idea', 'reason': 'future_idea', 'execution': None, 'values': {}, 'evidence': {}}
    def uncertain(reason):
        return {'status': 'uncertain', 'reason': reason, 'execution': None, 'values': {},
            'evidence': {'proposal': proposal if isinstance(proposal, dict) else None}}
    if not isinstance(proposal, dict) or proposal.get('status') not in {'linked', 'unplanned'}:
        return uncertain('attribution_unclear')
    evidence = proposal.get('evidence')
    evidence_message = source_reference(evidence, messages)
    if evidence_message is None or evidence_message.position > source.position:
        return uncertain('attribution_source_invalid')
    relation = proposal.get('relation')
    if relation not in {'new', 'existing'}:
        return uncertain('execution_relation_unclear')
    occurred = occurrence_day(item.get('occurred_at_text'), source)
    supplied_day = proposal.get('occurred_on')
    if supplied_day and supplied_day != (occurred.isoformat() if occurred else None):
        return uncertain('occurrence_time_not_sourced')
    values = {'goal_id': proposal.get('goal_id') or None, 'plan_id': proposal.get('plan_id') or None,
        'cycle_id': proposal.get('cycle_id') or None, 'occurred_on': occurred,
        'occurred_at_text': item.get('occurred_at_text')}
    from .goal_scope import goal_filter, isolated
    owned_filter = await goal_filter(db, user_id, source.conversation_id)
    test_isolated = await isolated(db, user_id)
    executions, goals, plans, cycles = (schema.tables[name] for name in (
        'pa_executions', 'pa_goals', 'module_two_record', 'pa_cycles'))
    execution = None
    if relation == 'existing':
        execution = (await db.execute(select(executions).where(executions.c.id == proposal.get('execution_id'),
            executions.c.user_id == user_id,
            executions.c.source_conversation_id == source.conversation_id if test_isolated else True))).mappings().one_or_none()
        if execution is None:
            return uncertain('execution_not_owned')
        for key in ('goal_id', 'plan_id', 'cycle_id'):
            if values[key] is not None and values[key] != execution[key]:
                return uncertain('execution_scope_conflict')
            values[key] = execution[key]
        if occurred and execution['occurred_on'] and occurred != execution['occurred_on']:
            return uncertain('different_occurrence_date')
        # A supplement is not permission to retrospectively assign an unknown
        # old execution to a new cycle, or to rewrite its occurrence date.
        values['occurred_on'] = execution['occurred_on']
    elif proposal.get('execution_id'):
        return uncertain('new_execution_cannot_reuse_id')
    if proposal['status'] == 'unplanned':
        if any(values[key] for key in ('goal_id', 'plan_id', 'cycle_id')):
            return uncertain('unplanned_scope_conflict')
    else:
        goal = (await db.execute(select(goals).where(goals.c.id == values['goal_id'],
            owned_filter))).mappings().one_or_none()
        if not goal:
            return uncertain('goal_not_owned')
        plan = None
        if values['plan_id']:
            plan = (await db.execute(select(plans).where(plans.c.id == values['plan_id'],
                plans.c.goal_id == goal['id'], plans.c.confirmation_status == 'confirmed',
                plans.c.confirmation_message_id.is_not(None)))).mappings().one_or_none()
            if not plan:
                return uncertain('plan_not_confirmed_for_goal')
            if occurred and _day(plan['created_at']) and occurred < _day(plan['created_at']):
                return uncertain('occurrence_precedes_plan')
        if values['cycle_id']:
            cycle = (await db.execute(select(cycles).where(cycles.c.id == values['cycle_id'],
                cycles.c.goal_id == goal['id']))).mappings().one_or_none()
            if not cycle or not plan or cycle['module_two_record_id'] != plan['id']:
                return uncertain('cycle_plan_mismatch')
            if relation == 'new':
                if occurred is None:
                    reference = proposal.get('cycle_reference')
                    from .models import ConversationMessage, Conversation
                    anchor = (await db.execute(select(ConversationMessage).join(Conversation,
                        Conversation.id == ConversationMessage.conversation_id).where(
                            Conversation.subject_id == user_id, ConversationMessage.id == plan['confirmation_message_id'],
                            ConversationMessage.role == 'user'))).scalar_one_or_none()
                    current = schema.tables['conversation_runtime_states']
                    current_binding = await db.scalar(select(current.c.conversation_id).where(
                        current.c.conversation_id == source.conversation_id,
                        current.c.active_goal_id == goal['id'], current.c.active_cycle_id == cycle['id']))
                    # Concrete authenticated plan confirmation plus a semantic
                    # source reference can identify a current occasion without
                    # inventing a date. Same-name histories need a second user
                    # source explicitly disambiguating the selected plan.
                    alternatives = await db.scalar(select(plans.c.id).join(goals, goals.c.id == plans.c.goal_id).where(
                        owned_filter, plans.c.id != plan['id'],
                        plans.c.confirmation_status == 'confirmed', plans.c.activity_content == plan['activity_content']).limit(1))
                    if (not current_binding or cycle['status'] not in {'planning','waiting_execution','reviewing'}
                            or not isinstance(reference, dict) or anchor is None
                            or reference.get('message_id') != anchor.id or not isinstance(reference.get('quote'), str)
                            or not reference['quote'] or reference['quote'] not in anchor.content
                            or anchor.created_at > source.created_at):
                        return uncertain('cycle_occurrence_reference_unclear')
                    if test_isolated and anchor.conversation_id != source.conversation_id:
                        return uncertain('cycle_occurrence_reference_unclear')
                    if alternatives:
                        disambiguation = source_reference(proposal.get('disambiguation_ref'), messages)
                        if disambiguation is None or disambiguation.position > source.position or disambiguation.created_at < anchor.created_at:
                            return uncertain('cycle_occurrence_reference_unclear')
                start = _day(cycle['created_at'])
                end = _day(cycle['completed_at'])
                if occurred and ((start and occurred < start) or (end and occurred > end)):
                    return uncertain('occurrence_outside_cycle')
    return {'status': proposal['status'], 'reason': 'source_verified', 'execution': execution,
        'values': values, 'evidence': {'relation': relation, 'source': evidence,
            'time_source_message_id': source.id, 'time_quote': item.get('occurred_at_text')}}


async def attribute_observation(db, *, user_id, event, item, source, messages):
    executions, links = schema.tables['pa_executions'], schema.tables['pa_activity_attributions']
    resolved = await _resolve(db, user_id=user_id, item=item, source=source, messages=messages)
    execution = resolved['execution']
    if resolved['status'] in {'linked', 'unplanned'} and execution is None:
        # Stable across retries/re-extraction. Never merge distinct messages by
        # activity name, date alone, or similarity of user wording.
        execution = (await db.execute(select(executions).where(executions.c.user_id == user_id,
            executions.c.origin_message_id == source.id,
            executions.c.origin_event_index == event['event_index']))).mappings().one_or_none()
        values = {**resolved['values'], 'activity_content': item['activity_content']}
        if execution:
            # Retrying the same extraction cannot rewrite an established
            # execution identity and silently move other observations with it.
            if any(execution[k] != values[k] for k in ('goal_id', 'plan_id', 'cycle_id', 'occurred_on')):
                resolved.update(status='uncertain', reason='origin_identity_conflict')
                execution = None
        else:
            execution = {'id': new_id(), 'user_id': user_id, **values,
                'source_conversation_id': source.conversation_id,
                'origin_message_id': source.id, 'origin_event_index': event['event_index']}
            await db.execute(insert(executions), execution)
    values = {'execution_id': execution['id'] if execution else None,
        'attribution_status': resolved['status'], 'reason_code': resolved['reason'], 'evidence': resolved['evidence']}
    exists = await db.scalar(select(links.c.event_id).where(links.c.event_id == event['id']))
    if exists:
        await db.execute(update(links).where(links.c.event_id == event['id']).values(**values, updated_at=now()))
    else:
        await db.execute(insert(links), {'event_id': event['id'], **values})
    # The old projection remains useful to existing UI, but only a validated
    # attribution can now populate its goal/cycle links.
    await db.execute(update(schema.tables['pa_activity_events']).where(
        schema.tables['pa_activity_events'].c.id == event['id']).values(
            goal_id=execution['goal_id'] if execution else None,
            cycle_id=execution['cycle_id'] if execution else None))
    return {**values, 'event_id': event['id']}


async def review_execution_binding(db, *, user_id, state, data, existing=None):
    """A new current review requires a current, sourced execution identity."""
    raw = data.get('m4_contract') or {}
    ref = raw.get('execution_ref') if isinstance(raw, dict) else None
    if not isinstance(ref, dict):
        # Once bound, later education/ABC confirmations may retain that exact
        # identity, but a new phase-B account must explicitly name its occasion.
        old = (((existing or {}).get('phase_c') or {}).get('_m4_contract') or {})
        old_ref = old.get('execution_ref')
        new_quote = raw.get('phase_b_quote') if isinstance(raw, dict) else None
        old_quote = ((old.get('evidence') or {}).get('phase_b_quote') or {}).get('quote')
        if old_ref and (not new_quote or new_quote == old_quote):
            ref = old_ref
        else:
            return {'ready': False, 'reason': 'review_execution_unclear'}
    events, links, executions = (schema.tables[n] for n in (
        'pa_activity_events', 'pa_activity_attributions', 'pa_executions'))
    query = select(executions, events.c.id.label('event_id'), events.c.source_quote,
        events.c.source_message_id, events.c.event_kind, events.c.activity_content.label('observation_activity'))\
        .join(links, links.c.execution_id == executions.c.id).join(events, events.c.id == links.c.event_id)\
        .where(executions.c.user_id == user_id, events.c.user_id == user_id,
            events.c.status == 'active', links.c.attribution_status == 'linked')
    if ref.get('execution_id'):
        query = query.where(executions.c.id == ref['execution_id'])
    elif (type(ref.get('source_message_id')) is int and ref.get('source_quote') and ref.get('activity_content')):
        query = query.where(events.c.source_message_id == ref['source_message_id'],
            events.c.source_quote == ref['source_quote'], events.c.activity_content == ref['activity_content'])
    else:
        return {'ready': False, 'reason': 'review_execution_reference_invalid'}
    from .goal_scope import isolated
    if await isolated(db, user_id):
        query = query.where(executions.c.source_conversation_id == state['conversation_id'],
            events.c.source_conversation_id == state['conversation_id'])
    rows = (await db.execute(query)).mappings().all()
    if not rows or len({row['id'] for row in rows}) != 1:
        return {'ready': False, 'reason': 'review_execution_not_found'}
    execution = rows[0]
    if execution['goal_id'] != state['active_goal_id'] or execution['cycle_id'] != state['active_cycle_id']:
        return {'ready': False, 'reason': 'feedback_belongs_to_other_execution'}
    phase_quote = raw.get('phase_b_quote')
    if isinstance(phase_quote, dict):
        phase_quote = phase_quote.get('quote')
    if phase_quote and not any(phase_quote in r['source_quote'] or r['source_quote'] in phase_quote for r in rows):
        return {'ready': False, 'reason': 'review_phase_b_not_from_execution'}
    overt = (data.get('phase_b') or {}).get('overt') or {}
    kinds = {r['event_kind'] for r in rows}
    if ((overt.get('action_taken') is True and 'performed' not in kinds)
            or (overt.get('action_taken') is False and 'not_performed' not in kinds)):
        return {'ready': False, 'reason': 'review_execution_result_conflict'}
    return {'ready': True, 'execution_id': execution['id'], 'goal_id': execution['goal_id'],
        'plan_id': execution['plan_id'], 'cycle_id': execution['cycle_id']}


async def attribution_context(db, *, user_id, conversation_id):
    events, links = schema.tables['pa_activity_events'], schema.tables['pa_activity_attributions']
    rows = (await db.execute(select(events.c.activity_content, events.c.source_quote,
        events.c.occurred_at_text, events.c.goal_id, events.c.cycle_id,
        links.c.attribution_status, links.c.reason_code).join(links, links.c.event_id == events.c.id)
        .where(events.c.user_id == user_id, events.c.source_conversation_id == conversation_id,
            events.c.status == 'active').order_by(events.c.created_at.desc(), events.c.id).limit(8))).mappings().all()
    return [dict(r) for r in rows]


async def delete_activity_dependencies(db, *, user_id, conversation_id=None, goal_ids=None):
    """Delete before source rows/goals; never leave another chat's evidence dangling."""
    events, links, executions = (schema.tables[n] for n in (
        'pa_activity_events', 'pa_activity_attributions', 'pa_executions'))
    event_filter = events.c.user_id == user_id
    execution_filter = executions.c.user_id == user_id
    if conversation_id is not None:
        event_filter = event_filter & (events.c.source_conversation_id == conversation_id)
        execution_filter = execution_filter & (executions.c.source_conversation_id == conversation_id)
    elif goal_ids is not None:
        event_filter = event_filter & events.c.goal_id.in_(goal_ids)
        execution_filter = execution_filter & executions.c.goal_id.in_(goal_ids)
    removed_ids = select(executions.c.id).where(execution_filter)
    # If an origin is deleted, retain another chat's raw observation as
    # unassigned; deletion must not recreate the removed originating fact.
    affected_events = [row[0] for row in (await db.execute(select(links.c.event_id).where(links.c.execution_id.in_(removed_ids)))).all()]
    if affected_events:
        await db.execute(update(events).where(events.c.id.in_(affected_events)).values(goal_id=None, cycle_id=None))
    await db.execute(update(links).where(links.c.execution_id.in_(removed_ids)).values(
        execution_id=None, attribution_status='uncertain', reason_code='execution_source_deleted', evidence={}))
    await db.execute(delete(links).where(links.c.event_id.in_(select(events.c.id).where(event_filter))))
    await db.execute(delete(executions).where(execution_filter))
