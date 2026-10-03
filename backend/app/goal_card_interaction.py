"""Native M2 UI actions, separate from module routing and clinical records."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select, update

from .database_v2_schema import metadata as schema
from .models import ConversationMessage


GOAL_CARD_POLICY = '''# 网页目标卡交互（仅改变目标制定的交互方式）
M2 前面的 PA 介绍、意愿确认及必要的价值探索仍按模块提示词执行；不能一进入 M2 就展开卡片。
用户明确愿意尝试、进入核心目标制定时调用 open_goal_card(primary)；无意愿先价值探索，形成愿意尝试的方向后再调用。
用户主动希望额外新增活动、已澄清不是替换核心时，调用 open_goal_card(secondary)。不要主动增加次要目标。
网页会在聊天上方展开卡片，让用户先填已想到的信息；可以部分填写、收起或继续聊天，不要求先填满。
核心制定阶段原有逐项问答改为：承接表单和已经说过的信息，按实际问题讨论细化；不从5.1重新逐题问，不把每个空格变成追问任务。
先 get_pa_card 看实际 goal_card 与真实消息来源。表单是用户草稿，不是确认；不要编造空值，也不要用旧记录盖掉用户本次修改。
核心草稿用 save_pa_card 保存已讨论的真实安排；再 review_goal_card 核验当前版本是否为 PA、是否三天内可做、用户难度是否不高于5。全部真实字段和版本核验通过后，该工具会直接展示供用户确认的卡片，不需要承诺下一轮再生成。远期或高难度先一起调整；不属于PA先澄清活动。
用户已明确表示当前计划没有障碍时，barrier_coping_plan用有真实消息ID和原文的not_applicable项，potential_barriers保存同一句原文，不编造应对方案。未知空白仍不代表无障碍。
评分、时间、障碍和应对已给过就直接使用；用户说不用继续细化/不想制定时 pause_goal_card 保留草稿，停止追问，不伪称确认或推进。
次要卡使用 update_secondary_goal_card 更新用户已说的信息，review_goal_card 只要求PA有效，不索要难度、障碍或完整核心流程。
review_goal_card 的结论必须根据实际草稿和当前对话；不为让工具通过而虚构已核验。修改卡片后旧核验失效，重新核验新版。
核心 review_goal_card 全部通过会直接展示；当前版本已有有效核验且 get_pa_card 的 goal_card_readiness.ready_for_display 为 true 时，也可直接 present_pa_card。次要用 present_secondary_goal_card，返回的 display_text 自动展示同一版摘要。
只有用户明确同意该展示版本才调用 confirm_pa_card / confirm_secondary_goal_card。修改或停止不是同意。
用户点击确认按钮时，get_pa_card 中 goal_card_action 会给出确切卡片及版本；直接确认该版本，不再保存、重审或重新展示导致版本变化。普通聊天提出的修改仍按修改流程处理。
成功确认后卡片自动收起，并明确说明目标信息保存在“我的目标”；工具未成功不得声称已保存。
'''


def tool_definitions():
    def tool(name, description, extra, required=()):
        props = {'state_version': {'type': 'integer'}, **extra}
        return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': props,
                'required': ['state_version', *required], 'additionalProperties': False}}}
    ref = {'card_id': {'type': 'string'}, 'card_revision': {'type': 'integer'}}
    evidence = {'source_message_id': {'type': 'integer'}, 'source_quote': {'type': 'string'}}
    return [
        tool('open_goal_card', '只在进入核心/次要目标制定阶段展开可部分填写的卡片。M2介绍或无意愿价值探索时不能调用；必须引用用户愿意制定/新增的真实原话。',
            {'kind': {'type': 'string', 'enum': ['primary', 'secondary']}, **evidence},
            ('kind', 'source_message_id', 'source_quote')),
        tool('review_goal_card', '核验当前草稿版本的PA、近期性与难度；核心卡全部字段/来源/版本均通过时立即返回ready_to_display和确认卡，不用再调用present。否则返回具体missing_fields和blockers，只修正对应项；不自动保存或代替用户确认。次要目标不要求难度或完整时间。',
            {**ref, 'is_pa': {'type': 'boolean'}, 'near_term': {'type': ['boolean', 'null']},
             'manageable': {'type': ['boolean', 'null']},
             'concerns': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 6}},
            ('card_id', 'card_revision', 'is_pa', 'near_term', 'manageable', 'concerns')),
        tool('update_secondary_goal_card', '仅修改次要卡：每个字段都要引用用户原文，不影响核心目标。表单已经保存的内容无需再抄一遍。',
            {**ref, 'updates': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'field': {'type': 'string', 'enum': ['activity_content', 'schedule_text', 'location', 'frequency_text', 'long_term_direction']},
                'value': {'type': 'string'}, **evidence},
                'required': ['field', 'value', 'source_message_id', 'source_quote'], 'additionalProperties': False}}},
            ('card_id', 'card_revision', 'updates')),
        tool('present_secondary_goal_card', '展示已核验的次要卡，请用户确认基本记录；不启动核心周期，不要求难度或障碍字段。', ref, ('card_id', 'card_revision')),
        tool('confirm_secondary_goal_card', '用户明确同意刚刚展示的同一版本次要卡时保存；不修改核心目标。', ref, ('card_id', 'card_revision')),
        tool('pause_goal_card', '用户明确不需要继续细化时保留草稿并停止追问；不是确认，不删除已有目标。', evidence,
            ('source_message_id', 'source_quote')),
    ]


def reject(reason):
    # Lazy import avoids the PA executor / UI adapter import cycle.
    from .pa_card_tools import ToolRejected
    raise ToolRejected(reason)


async def source(db, conversation, boundary, args):
    message = await db.get(ConversationMessage, args.get('source_message_id'))
    quote = args.get('source_quote')
    if (not message or message.conversation_id != conversation.id or message.role != 'user'
            or message.position > boundary.position or not isinstance(quote, str)
            or not quote.strip() or quote not in message.content):
        reject('goal_card_invalid_user_source')
    return message


async def current_card(db, conversation, args=None):
    from .goal_card_workspace import read_card_row
    row = await read_card_row(db, conversation.id, conversation.subject_id, lock=True)
    if not row:
        reject('goal_card_not_opened')
    if args and (row['id'] != args.get('card_id') or row['revision'] != args.get('card_revision')):
        reject('goal_card_changed_requery')
    return row


def review_passes(row):
    review = row.get('review') or {}
    if review.get('revision') != row['revision'] or review.get('is_pa') is not True or row.get('concerns'):
        return False
    if row['kind'] == 'secondary':
        return bool((row.get('fields') or {}).get('activity_content'))
    fields = row.get('fields') or {}
    rating = fields.get('difficulty_rating')
    return (review.get('near_term') is True and review.get('manageable') is True
        and bool(fields.get('activity_content')) and bool(fields.get('schedule_text'))
        and type(rating) is int and 0 <= rating <= 5)


async def presentation_readiness(db, conversation, state, *, row=None, pending=None):
    """One owned snapshot of the real gates used by query, review and present."""
    from .goal_card_workspace import read_card_row, _plan_fields
    from .program_confirmation import draft
    from .plan_contract import missing_plan_fields
    row = row or await read_card_row(db, conversation.id, conversation.subject_id)
    blockers, missing = [], []
    if not row:
        return {'ready_for_display': False, 'review_current': False,
            'missing_fields': [], 'blockers': ['goal_card_not_opened']}
    review = row.get('review') or {}
    review_current = review.get('revision') == row['revision']
    if row['phase'] in {'paused', 'confirmed'}:
        blockers.append('goal_card_closed_or_paused')
    if not review_current:
        blockers.append('current_version_not_reviewed')
    elif not review_passes(row):
        blockers.append('pa_time_difficulty_review_not_passed' if row['kind'] == 'primary' else 'pa_review_not_passed')
    if row.get('submission_text') and not row.get('submission_message_id'):
        blockers.append('form_not_submitted_to_conversation')
    if row['kind'] == 'primary':
        pending = pending or await draft(db, state, conversation.subject_id)
        if pending is None:
            blockers.append('plan_not_saved')
        else:
            missing = missing_plan_fields(pending)
            if row.get('plan_id') != pending['id'] or any((row.get('fields') or {}).get(key) != value
                    for key, value in _plan_fields(pending).items()):
                blockers.append('reviewed_form_differs_from_saved_plan')
    elif not (row.get('fields') or {}).get('activity_content'):
        missing.append('activity_content')
    return {'ready_for_display': not blockers and not missing,
        'review_current': review_current, 'missing_fields': missing, 'blockers': blockers}


async def execute_ui_tool(db, conversation, state, user, name, args, *, provider,
                          following_assistant_message_id=None, confirmation_evidence=None):
    from .goal_card_workspace import (open_card, pause_card, record_card_update, public_card)
    if name == 'open_goal_card':
        evidence = await source(db, conversation, user, args)
        from .goal_card_workspace import read_card_row
        prior = await read_card_row(db, conversation.id, conversation.subject_id, lock=True)
        if prior and prior['phase'] in {'paused', 'confirmed'} and evidence.id != user.id:
            reject('resume_or_new_card_requires_current_user_source')
        card = await open_card(db, conversation, state, args['kind'], user.id,
            following_assistant_message_id=following_assistant_message_id)
        return {'status': 'goal_card_opened', 'goal_card': card, 'confirmed': False}
    if name == 'pause_goal_card':
        evidence = await source(db, conversation, user, args)
        if evidence.id != user.id:
            reject('pause_requires_current_user_message')
        card = await pause_card(db, conversation, state, user.id,
            following_assistant_message_id=following_assistant_message_id)
        return {'status': 'goal_card_paused', 'goal_card': card, 'confirmed': False}
    names = {'review_goal_card', 'update_secondary_goal_card', 'present_secondary_goal_card', 'confirm_secondary_goal_card'}
    if name not in names:
        return None
    row = await current_card(db, conversation, args)
    await check_current_action(db, conversation, user, row)
    if row['phase'] in {'paused', 'confirmed'}:
        reject('goal_card_closed_or_paused')
    if name == 'review_goal_card':
        if (type(args.get('is_pa')) is not bool
                or (args.get('near_term') is not None and type(args['near_term']) is not bool)
                or (args.get('manageable') is not None and type(args['manageable']) is not bool)
                or not isinstance(args.get('concerns'), list)
                or len(args['concerns']) > 6
                or any(not isinstance(s, str) or len(s) > 1000 for s in args['concerns'])):
            reject('invalid_goal_card_review')
        if row.get('submission_text') and not row.get('submission_message_id'):
            reject('form_not_submitted_to_conversation')
        review = {k: args[k] for k in ('is_pa', 'near_term', 'manageable')}
        previous = row.get('review') or {}
        unchanged = (previous.get('revision') == row['revision']
            and all(previous.get(key) == value for key, value in review.items())
            and row.get('concerns') == args['concerns'])
        if not unchanged:
            review.update(revision=row['revision'] + 1, user_message_id=user.id)
            row = await record_card_update(db, row, {'review': review, 'concerns': args['concerns'],
                'phase': 'discussing', 'display_text': None, 'display_assistant_message_id': None},
                reason='ai_reviewed_current_form', message_id=user.id)
        readiness = await presentation_readiness(db, conversation, state, row=row)
        return {'status': 'goal_card_reviewed', 'ready': readiness['ready_for_display'],
            'readiness': readiness, 'goal_card': public_card(row)}
    if row['kind'] != 'secondary':
        reject('secondary_operation_requires_secondary_card')
    if name == 'update_secondary_goal_card':
        from .goal_card_workspace import CardFields
        fields = dict(row['fields'] or {})
        updates = args.get('updates')
        allowed = {'activity_content', 'schedule_text', 'location', 'frequency_text', 'long_term_direction'}
        if not isinstance(updates, list) or not 1 <= len(updates) <= 5:
            reject('invalid_secondary_updates')
        for entry in updates:
            if not isinstance(entry, dict) or entry.get('field') not in allowed:
                reject('invalid_secondary_updates')
            await source(db, conversation, user, entry)
            value = entry.get('value')
            if not isinstance(value, str) or not value.strip() or value not in entry['source_quote']:
                reject('secondary_value_not_in_user_source')
            fields[entry['field']] = value
        fields = CardFields.model_validate(fields).model_dump()
        row = await record_card_update(db, row, {'fields': fields, 'phase': 'discussing',
            'review': {}, 'concerns': [], 'display_text': None, 'display_assistant_message_id': None},
            reason='secondary_updated_from_user', message_id=user.id)
        return {'status': 'goal_card_updated', 'goal_card': public_card(row)}
    if name == 'present_secondary_goal_card':
        if not review_passes(row):
            reject('secondary_card_requires_current_pa_review')
        from .goal_card_workspace import LABELS
        lines = ['次要 PA 目标卡（额外活动，原核心目标保持不变）']
        for key, label in LABELS.items():
            value = (row.get('fields') or {}).get(key)
            if value is not None and value != '':
                lines.append(f'{label}：{value}')
        lines.append('这张卡是否符合你的意思？确认后会保存在“我的目标”。')
        text = '\n'.join(lines)
        row = await record_card_update(db, row, {'phase': 'ready', 'display_text': text,
            'display_user_message_id': user.id, 'display_assistant_message_id': None,
            'review': {**row['review'], 'revision': row['revision'] + 1}},
            reason='secondary_presented', message_id=user.id)
        return {'status': 'ready_to_display', 'display_text': text, 'goal_card': public_card(row), 'confirmed': False}
    previous = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation.id, ConversationMessage.position < user.position)
        .order_by(ConversationMessage.position.desc(), ConversationMessage.id.desc()).limit(1))).scalar_one_or_none()
    if (row['phase'] != 'ready' or not review_passes(row) or not previous
            or previous.role != 'assistant' or row.get('display_assistant_message_id') != previous.id
            or not row.get('display_text') or row['display_text'] not in previous.content):
        reject('secondary_confirmation_display_changed')
    from .dialogue_confirmation import affirmative
    from .confirmation_intent import semantic_confirmation
    if not affirmative(user.content):
        accepted = (confirmation_evidence.matches(user=user, assistant=previous, module='module_2')
            if confirmation_evidence is not None else await semantic_confirmation(provider,
                user_text=user.content, assistant_text=previous.content, module='module_2'))
        if not accepted:
            reject('secondary_confirmation_not_verified')
    row = await record_card_update(db, row, {'phase': 'confirmed', 'confirmation_message_id': user.id},
        reason='user_confirmed_secondary', message_id=user.id)
    return {'status': 'secondary_confirmed', 'goal_card': public_card(row),
            'core_unchanged': True, 'saved_location': '我的目标'}


async def before_core_operation(db, conversation, state, name):
    if name not in {'save_pa_card', 'present_pa_card', 'confirm_pa_card'}:
        return
    row = await current_card(db, conversation)
    if row['kind'] != 'primary' or row['phase'] in {'paused', 'confirmed'}:
        reject('core_card_not_in_formulation')
    if name == 'confirm_pa_card' and not review_passes(row):
        reject('core_card_requires_current_pa_time_difficulty_review')
    if name == 'confirm_pa_card' and row['phase'] != 'ready':
        reject('core_card_not_presented')


async def bind_card_action(db, conversation, message_id, metadata):
    if metadata.get('goal_card_action') not in {'confirm', 'pause', 'submit'}:
        return
    from .goal_card_workspace import read_card_row
    row = await read_card_row(db, conversation.id, conversation.subject_id, lock=True)
    if (not row or row['id'] != metadata.get('goal_card_id')
            or str(row['revision']) != metadata.get('goal_card_revision')):
        raise HTTPException(409, '目标卡已更新')
    await db.execute(schema.tables['ai_decision_logs'].insert(), {
        'conversation_id': conversation.id, 'turn_id': str(message_id),
        'module_name': 'module_2', 'decision_type': 'goal_card_user_action',
        'decision_value': {'card_id': row['id'], 'revision': row['revision'],
                           'action': metadata['goal_card_action']},
        'evidence_message_ids': [message_id]})


async def current_user_action(db, conversation, message_id):
    logs = schema.tables['ai_decision_logs']
    return (await db.execute(select(logs.c.decision_value).where(
        logs.c.conversation_id == conversation.id, logs.c.turn_id == str(message_id),
        logs.c.decision_type == 'goal_card_user_action').order_by(logs.c.id.desc()).limit(1))).scalar_one_or_none()


def confirmation_button_text(kind, revision):
    """Exact text emitted by GoalFormulationPanel; free chat is not a button."""
    label = '核心目标' if kind == 'primary' else '次要目标'
    return f'确认这张{label}卡（第 {revision} 版），就按这个安排。'


async def structured_confirmation_evidence(db, conversation, user, *, row=None):
    """Validate the durable UI action and displayed version without an LLM.

    Called again inside the mutation transaction. The existing clinical
    confirmation service still validates the actual plan and its fingerprint.
    """
    action = await current_user_action(db, conversation, user.id)
    if not action or action.get('action') != 'confirm':
        return None
    row = row or await current_card(db, conversation)
    if user.content != confirmation_button_text(row['kind'], action.get('revision')):
        return None
    await check_current_action(db, conversation, user, row)
    previous = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.position < user.position).order_by(
            ConversationMessage.position.desc(), ConversationMessage.id.desc()).limit(1))).scalar_one_or_none()
    if (row['phase'] != 'ready' or not review_passes(row) or not previous
            or previous.role != 'assistant' or row.get('display_assistant_message_id') != previous.id
            or not row.get('display_text') or row['display_text'] not in previous.content):
        reject('goal_card_confirmation_display_changed')
    from .confirmation_intent import ConfirmationEvidence
    return ConfirmationEvidence(user.id, previous.id, user.content, previous.content, 'module_2', True)


async def check_current_action(db, conversation, user, row=None, *, operation=None):
    action = await current_user_action(db, conversation, user.id)
    if not action or action.get('action') != 'confirm':
        return
    row = row or await current_card(db, conversation)
    if action.get('card_id') != row['id'] or action.get('revision') != row['revision']:
        reject('goal_card_confirmation_revision_changed')
    # Preserve the version from an explicit UI confirmation even if a model
    # proposes a redundant save/review first. Plain chat corrections have no
    # confirm action receipt and retain the normal editing tools.
    expected = 'confirm_pa_card' if row['kind'] == 'primary' else 'confirm_secondary_goal_card'
    if operation is not None and operation != expected:
        reject('goal_card_confirmation_pending_use_confirm')


async def mark_core_presented(db, conversation, state, pending, text, boundary):
    from .goal_card_workspace import record_card_update, _plan_fields
    row = await current_card(db, conversation)
    # The UI review must describe the real stored plan, not another proposal.
    if row.get('plan_id') != pending['id'] or any((row.get('fields') or {}).get(k) != v
            for k, v in _plan_fields(pending).items()):
        reject('reviewed_form_differs_from_saved_plan')
    await record_card_update(db, row, {'phase': 'ready', 'display_text': text,
        'display_user_message_id': boundary, 'display_assistant_message_id': None,
        'review': {**row['review'], 'revision': row['revision'] + 1}}, reason='core_presented', message_id=boundary)


async def finalize_ui_display(db, *, session_id, user_id, assistant_message_id):
    from .v2_workflow import runtime_for
    from .goal_card_workspace import read_card_row
    conversation, state = await runtime_for(db, session_id)
    if not conversation or conversation.subject_id != user_id:
        return
    row = await read_card_row(db, conversation.id, user_id, lock=True)
    assistant = await db.get(ConversationMessage, assistant_message_id)
    if (not row or row['phase'] != 'ready' or not assistant or assistant.role != 'assistant'
            or assistant.conversation_id != conversation.id or not row.get('display_text')
            or row['display_text'] not in assistant.content):
        return
    previous = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation.id, ConversationMessage.position < assistant.position)
        .order_by(ConversationMessage.position.desc()).limit(1))).scalar_one_or_none()
    latest = await db.scalar(select(ConversationMessage.id).where(ConversationMessage.conversation_id == conversation.id)
        .order_by(ConversationMessage.position.desc()).limit(1))
    if not previous or previous.id != row.get('display_user_message_id') or latest != assistant.id:
        return
    table = schema.tables['goal_card_workspaces']
    await db.execute(update(table).where(table.c.id == row['id'], table.c.revision == row['revision'])
        .values(display_assistant_message_id=assistant.id))


async def validate_card_action(db, *, session_id, user_id, metadata, text):
    if not any(key.startswith('goal_card_') for key in metadata):
        return
    from .config import get_settings
    from .goal_card_workspace import read_card_row
    from .v2_workflow import runtime_for
    if not get_settings().goal_card_ui_enabled:
        raise HTTPException(409, '目标卡暂未开启')
    conversation, state = await runtime_for(db, session_id)
    if not conversation or conversation.subject_id != user_id or state['current_module'] != 'module_2':
        raise HTTPException(409, '当前目标阶段已变化，请刷新后再操作')
    row = await read_card_row(db, conversation.id, user_id, lock=True)
    if (not row or row['id'] != metadata.get('goal_card_id')
            or str(row['revision']) != metadata.get('goal_card_revision')):
        raise HTTPException(409, '目标卡已更新，请查看最新版本后再操作')
    action = metadata.get('goal_card_action')
    if action == 'submit' and row['phase'] == 'discussing' and text == row.get('submission_text'):
        return
    if action == 'confirm' and row['phase'] == 'ready' and review_passes(row):
        return
    if action == 'pause' and row['phase'] in {'formulating', 'discussing', 'ready'}:
        return
    raise HTTPException(409, '当前卡片不能执行此操作，请继续讨论或重新查看')
