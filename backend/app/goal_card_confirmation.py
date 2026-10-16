"""The shared read/commit contract for a version-bound goal-card button.

Plain conversational consent still uses the adjacent-message interpreter.
A button instead names an owned, reviewed card and its durable display anchor.
Nothing in this module commits, changes stages, or interprets text as consent.
"""
from __future__ import annotations

import re
from sqlalchemy import select

from .models import ConversationMessage


MESSAGES = {
    'goal_card_not_ready': '这版目标卡尚未完成核对，暂时不能确认。',
    'goal_card_already_confirmed': '这张目标卡已经确认并保存，无需重复确认。',
    'goal_card_paused': '这张草稿已暂停，请恢复讨论并核对卡片后再确认。',
    'goal_card_scope_changed': '当前目标阶段已变化，请重新查看目标卡。',
    'goal_card_confirmation_revision_changed': '目标卡已有更新，请查看最新内容后再确认。',
    'goal_card_confirmation_display_changed': '这版目标卡尚未完整展示，请重新展示后再确认。',
    'goal_card_review_changed': '这版目标卡需要重新核对，暂未保存。',
    'goal_card_plan_changed': '当前计划与展示的卡片不一致，请核对最新卡片后再确认。',
    'goal_card_unresolved_change': '你在展示卡片后提出了修改或暂不确认，请先核对并重新展示卡片。',
    'goal_card_confirmation_not_explicit': '未收到这张卡片的明确确认操作，目标尚未保存。',
    'storage_error': '保存暂时失败，卡片仍保留，请稍后重试。',
}


def confirmation_message(code):
    return MESSAGES.get(code, '这次确认未完成，请重新读取目标卡并核对当前状态。')


def readiness_message(readiness):
    """Describe observed gates; missing data is not evidence of a running job."""
    blockers = readiness.get('blockers', [])
    if 'form_not_submitted_to_conversation' in blockers:
        return '草稿已暂存，还未交给教练核对。请打开卡片，点击“交给教练一起完善”。'
    if 'reviewed_form_differs_from_saved_plan' in blockers:
        return '卡片与计划草稿尚未同步，当前不能确认；已填写内容仍保留。'
    if 'plan_not_saved' in blockers:
        return '卡片内容已保留，但计划草稿尚未保存成功，当前不能确认。'
    if 'current_version_not_reviewed' in blockers:
        return '这版草稿尚未完成核验，当前不能确认；已填写内容仍保留。'
    labels = {'activity_content': '活动', 'schedule_text': '时间安排',
        'difficulty_rating': '执行难度', 'difficulty_evidence': '难度评分来源',
        'potential_barriers': '困难评估', 'barrier_coping_plan': '应对安排',
        'duration_minutes': '时长', 'frequency_rule': '频率', 'location': '地点'}
    missing = readiness.get('missing_fields', [])
    if missing:
        return '系统尚未核实' + '、'.join(labels.get(key, '计划信息') for key in missing) + '，当前不能确认。已说过的内容无需重新填写。'
    if 'schedule_in_past' in blockers:
        return '当前时间安排已被标记为过期，需要核对实际执行时间后再确认。'
    return '这版卡片的核验尚未通过，当前不能确认；已填写内容仍保留。'


def is_button_text(text, kind, revision):
    # Both deployed clients send authenticated metadata. Text alone is never
    # a button; callers must also validate its persisted action receipt.
    from .goal_card_interaction import confirmation_button_text
    label = '核心目标' if kind == 'primary' else '次要目标'
    return text in {confirmation_button_text(kind, revision), f'确认这张{label}卡，就按这个安排。'}


def unresolved_change(text):
    """Conservative explicit plan changes while a background update may lag.

    Status questions (refresh/where is the button) are not plan amendments.
    Actual saved edits are independently caught by version and fingerprint.
    """
    # A UI-status delay is not withdrawal of the plan ("先等等，网页在加载").
    # A real plan-specific instruction in the same message still takes priority.
    text = text or ''
    plan_change = bool(re.search(
        r'先不(?:确认|保存|做|执行)|不要(?:确认|保存|这个计划|这个安排)|别(?:确认|保存)|'
        r'(?:撤回|取消)(?:刚才的)?(?:确认|这个计划|这个安排|这张卡|目标|计划|安排|活动)|不做了|'
        r'(?:改成|改为|换成|改到)(?:明天|后天|今天|下周|周[一二三四五六日天]|\d|[一二三四五六七八九十]+分钟|散步|游泳|跑步|骑车|家务)|'
        r'(?:修改|调整|更换)(?:一下)?(?:这张|这个|目标|计划|安排|活动|时间|时长)|'
        r'(?:时间|时长|地点|活动|频率|难度).{0,8}(?:改|换)|'
        r"(?:do not|don't|not ready to|not yet) (?:confirm|save)|"
        r'(?:change|cancel|withdraw|revise) (?:this |the |my )?(?:plan|activity|schedule|confirmation)', text, re.I))
    return plan_change or bool(re.fullmatch(r'(?:我)?(?:先等等|还没决定)[。！!\s]*', text.strip()))


async def confirmation_status(db, conversation, state, row, *, user=None):
    """Read by GET, preflight and the locked writer; no adjacency gate."""
    result = {'allowed': False, 'code': None, 'message': None,
              'card_id': row['id'] if row else None,
              'revision': row['revision'] if row else None,
              'display_message_id': row.get('display_assistant_message_id') if row else None,
              'recoverable': False}

    def blocked(code):
        return {**result, 'code': code, 'message': confirmation_message(code)}

    if (not conversation or not state or not row or row['conversation_id'] != conversation.id
            or row['user_id'] != conversation.subject_id or state.get('conversation_id') != conversation.id
            or state.get('current_module') != 'module_2' or state.get('flow_status') in {'paused', 'completed'}
            or str((state.get('memory') or {}).get('sandbox_mode', '')).lower() == 'true'):
        return blocked('goal_card_scope_changed')
    if row['phase'] in {'confirmed', 'paused'}:
        return blocked('goal_card_already_confirmed' if row['phase'] == 'confirmed' else 'goal_card_paused')
    from .goal_card_interaction import presentation_readiness
    ready = await presentation_readiness(db, conversation, state, row=row)
    if row['phase'] != 'ready':
        return {**blocked('goal_card_not_ready'), 'message': readiness_message(ready), 'readiness': ready}
    if not ready['ready_for_display']:
        return {**blocked('goal_card_review_changed'), 'message': readiness_message(ready), 'readiness': ready}
    anchor = await db.get(ConversationMessage, row.get('display_assistant_message_id')) if row.get('display_assistant_message_id') else None
    source = await db.get(ConversationMessage, row.get('display_user_message_id')) if row.get('display_user_message_id') else None
    source_valid = bool(source and source.conversation_id == conversation.id and source.role == 'user')
    display_valid = bool(anchor and anchor.conversation_id == conversation.id and anchor.role == 'assistant'
        and source_valid and source.position < anchor.position
        and row.get('display_text') and row['display_text'] in anchor.content)
    if user is not None and (not display_valid or user.conversation_id != conversation.id
            or user.role != 'user' or user.position <= anchor.position):
        return blocked('goal_card_confirmation_display_changed')
    if not source_valid or not row.get('display_text'):
        return blocked('goal_card_confirmation_display_changed')
    if row['kind'] == 'primary':
        from .program_confirmation import draft
        from .dialogue_confirmation import fingerprint, render_confirmation_summary
        from .database_v2_schema import metadata as schema
        pending = await draft(db, state, conversation.subject_id)
        goals, cycles = schema.tables['pa_goals'], schema.tables['pa_cycles']
        goal = (await db.execute(select(goals).where(goals.c.id == state.get('active_goal_id'),
            goals.c.user_id == conversation.subject_id))).mappings().one_or_none()
        cycle = (await db.execute(select(cycles).where(cycles.c.id == state.get('active_cycle_id'),
            cycles.c.goal_id == state.get('active_goal_id')))).mappings().one_or_none()
        if (not pending or pending.get('record_status') != 'draft'
                or not goal or goal['status'] not in {'draft', 'active'}
                or not cycle or cycle['status'] != 'planning'
                or row.get('cycle_id') != state.get('active_cycle_id')
                or row.get('goal_id') != state.get('active_goal_id')
                or (row.get('review') or {}).get('display_fingerprint', fingerprint(pending)) != fingerprint(pending)
                or render_confirmation_summary('module_2', pending) != row['display_text']):
            return blocked('goal_card_plan_changed')
    query = select(ConversationMessage.content).where(
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.role == 'user',
        ConversationMessage.position > (anchor.position if display_valid else source.position))
    if user is not None:
        query = query.where(ConversationMessage.position < user.position)
    if any(unresolved_change(text) for text in (await db.execute(query)).scalars()):
        return blocked('goal_card_unresolved_change')
    if not display_valid:
        return {**blocked('goal_card_confirmation_display_changed'), 'recoverable': True}
    return {**result, 'allowed': True}
