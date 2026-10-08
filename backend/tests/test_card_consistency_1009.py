"""Regressions for the D card, using synthetic owners and isolated SQLite."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, insert, select, update

from app.answer_validator import validate_answer
from app.config import Settings
from app.database_v2_schema import metadata as schema
from app.goal_card_confirmation import confirmation_status
from app.knowledge_mediator import mediate_knowledge
from app.models import AIExecutionEvent, Conversation, ConversationMessage
from app.providers.base import Completion, StreamDelta
from app.reply_integrity import SentenceSaveGuard
from app.retrieval import KnowledgeChunk
from app.v2_repository import confirm_plan
from test_m2_time_and_card_readiness_1007 import card_db, executor, call, review, save_no_barrier, confirmed, SCHEDULE


QUOTES = [
    '分级任务指的是先从简单的活动开始，然后逐渐增加复杂性和难度。也就是说，循序渐进地设定活动，从简单到复杂，用这种方法设计活动可以让来访者在一开始努力的时候就获得成功。',
    '排除障碍就像是“微调”计划。“微调”指的是一旦制订了基本的行动计划，只需要来访者做出小小的改变。不需要在初步尝试（失败）后就彻底放弃解决方案，更重要的是修改和坚持，教来访者一点一点改变。',
]


async def mediate(quote, source=None, application='依据原文回应，不替用户决定。'):
    raw = json.dumps({'decision': 'use', 'selections': [
        {'id': 'test-kb', 'quote': quote, 'application': application}], 'note': ''}, ensure_ascii=False)
    class Provider:
        async def route_detailed(self, **kwargs):
            return Completion(text=raw, model='test-flash', usage={}, reasoning_content='', finish_reason='stop')
    return await mediate_knowledge(state={'user_input': '想先从简单活动开始。'}, module='module_2',
        knowledge=[KnowledgeChunk(id='test-kb', source='test', text=quote if source is None else source)],
        provider=Provider(), settings=Settings(_env_file=None, knowledge_mediator_enabled=True))


@pytest.mark.asyncio
@pytest.mark.parametrize('quote', QUOTES)
async def test_full_source_matched_82_and_93_character_quotes_are_preserved(quote):
    assert 80 < len(quote) < 100
    chunks, advice, metrics = await mediate(quote)
    assert metrics['status'] == 'completed'
    assert len(chunks) == 1 and metrics['selections'][0]['quote'] == quote
    assert quote in advice


@pytest.mark.asyncio
async def test_longer_quote_does_not_bypass_evidence_or_schema_checks():
    chunks, advice, metrics = await mediate(QUOTES[0], source='不相关原文')
    assert (chunks, advice, metrics['reason']) == ([], '', 'invalid_evidence')
    chunks, advice, metrics = await mediate('字' * 241)
    assert not chunks and not advice and metrics['reason'] == 'invalid_schema'
    assert metrics['validation_errors'] == [{'field': 'selections.0.quote', 'type': 'string_too_long'}]
    chunks, advice, metrics = await mediate('原文', application='字' * 71)
    assert not chunks and not advice
    assert metrics['validation_errors'][0]['field'] == 'selections.0.application'


@pytest.mark.parametrize('reply', [
    '那这份核心目标就算确认归档了。', '目标已确认归档。',
    '这份安排可以吗？如果确认没问题，点卡片上的“确认这个安排”。',
    '这是卡片还在后台更新整理，需要等它核对完成。',
])
def test_uncommitted_claim_is_held_across_stream_tokens(reply):
    authority = {'available': True, 'current_module': 'module_2', 'plan_confirmed': False,
        'goal_card_operation': {'status': 'not_running', 'confirmation': {'allowed': False}}}
    audit = validate_answer(reply=reply, module='module_2', evidence_ids=[], workflow=authority)
    assert 'uncommitted_workflow_claim' in {f['code'] for f in audit['findings']}
    guard = SentenceSaveGuard(authority, 'module_2')
    visible = ''.join(s for char in reply for s in guard.push(char))
    assert visible != reply
    assert guard.blocked


@pytest.mark.parametrize('reply', ['这份目标尚未确认归档。', '当前不能点击确认按钮。', '如果后台正在更新，可以稍后查看状态。'])
def test_accurate_negative_or_conditional_state_is_allowed(reply):
    audit = validate_answer(reply=reply, module='module_2', evidence_ids=[], workflow={
        'plan_confirmed': False, 'goal_card_operation': {'status': 'not_running', 'confirmation': {'allowed': False}}})
    assert 'uncommitted_workflow_claim' not in {f['code'] for f in audit['findings']}


def test_reviewed_display_can_invite_confirmation_but_is_not_a_save_receipt():
    authority = {'plan_confirmed': False, 'goal_card_operation': {
        'display_prepared_for_turn': True, 'confirmation': {'allowed': False}}}
    for reply, blocked in [('核对后可以点击卡片上的“确认这个安排”。', False), ('目标已确认归档。', True)]:
        audit = validate_answer(reply=reply,module='module_2',evidence_ids=[],workflow=authority)
        assert ('uncommitted_workflow_claim' in {f['code'] for f in audit['findings']}) is blocked


@pytest.mark.asyncio
async def test_submitted_form_cannot_skip_review_but_real_missing_data_is_not_invented(card_db):
    async with card_db() as db:
        await db.execute(update(schema.tables['goal_card_workspaces']).values(submission_message_id=101))
        await db.commit()
    tools = executor(card_db)
    result = await call(tools, 'continue_pa_conversation', {})
    assert result['reason'] == 'current_card_review_required'
    checked = await review(tools)
    assert checked['readiness']['missing_fields'] == ['barrier_coping_plan']
    assert (await call(tools, 'continue_pa_conversation', {}))['status'] == 'continue_conversation'
    assert (await call(tools, 'get_pa_card', {}))['draft']['barrier_coping_plan'] == []


@pytest.mark.asyncio
async def test_unsent_form_explains_submission_gate_instead_of_infinite_wait(card_db):
    async with card_db() as db:
        cards = schema.tables['goal_card_workspaces']
        await db.execute(update(cards).values(submission_text='草稿', submission_message_id=None))
        row = dict((await db.execute(select(cards))).mappings().one())
        state = dict((await db.execute(select(schema.tables['conversation_runtime_states']))).mappings().one())
        status = await confirmation_status(db, await db.get(Conversation, 1), state, row)
        assert not status['allowed']
        assert '交给教练一起完善' in status['message']
        assert '请等' not in status['message']


@pytest.mark.asyncio
async def test_confirm_updates_goal_title_atomically_from_exact_confirmed_plan(card_db):
    tools = executor(card_db)
    assert (await save_no_barrier(tools))['status'] == 'draft_saved'
    async with card_db() as db:
        plans, goals = schema.tables['module_two_record'], schema.tables['pa_goals']
        await db.execute(update(plans).values(activity_content='站桩'))
        assert (await db.execute(select(goals.c.title))).scalar_one() == '打羽毛球'
        await confirm_plan(db, user_id='a', goal_id='goal', cycle_id='cycle', plan_id='plan', message_id=101)
        assert (await db.execute(select(goals.c.title))).scalar_one() == '站桩'
        assert (await db.execute(select(plans.c.record_status))).scalar_one() == 'confirmed'
        await db.rollback()
    async with card_db() as db:
        assert (await db.execute(select(goals.c.title))).scalar_one() == '打羽毛球'
        assert (await db.execute(select(plans.c.record_status))).scalar_one() == 'draft'


@pytest.mark.asyncio
async def test_pre_reply_tools_review_then_durable_display_then_owned_confirmation(card_db, monkeypatch):
    from app import pa_background
    from app.pa_reply_preparation import prepare_pa_reply_node
    from app.pa_card_tools import finalize_tool_display
    from app.goal_card_interaction import finalize_ui_display
    tools = executor(card_db)
    assert (await save_no_barrier(tools))['status'] == 'draft_saved'
    async with card_db() as db:
        await db.run_sync(lambda session: AIExecutionEvent.__table__.create(session.connection()))
        await db.execute(delete(ConversationMessage).where(ConversationMessage.id == 102))
        await db.execute(update(schema.tables['goal_card_workspaces']).values(submission_message_id=101))
        await db.commit()

    class ToolProvider:
        name, model = 'stub', 'test-background'
        calls = 0
        async def stream_tools(self, **kwargs):
            self.calls += 1
            results = [json.loads(m['content']) for m in kwargs['messages'] if m['role'] == 'tool']
            if self.calls == 1:
                name, args = 'continue_pa_conversation', {}
            else:
                last = results[-1]
                assert last['reason'] == 'current_card_review_required'
                name, args = 'review_goal_card', {
                    'state_version': last['state_version'], 'card_id': last['card_id'],
                    'card_revision': last['card_revision'], 'is_pa': True, 'near_term': False,
                    'schedule_status': 'actionable', 'schedule_source': {
                        'source_message_id': 101, 'source_quote': SCHEDULE},
                    'manageable': True, 'concerns': []}
            yield StreamDelta(kind='tool_calls', tool_calls=[{'id': f'call-{self.calls}',
                'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}])
    provider = ToolProvider()
    monkeypatch.setattr(pa_background, 'background_provider', lambda _: provider)
    settings = Settings(_env_file=None, database_schema_version='v2', goal_card_ui_enabled=True)
    context = SimpleNamespace(settings=settings, sessionmaker=card_db,
        prompt_snapshot={'module_2': '遵循真实来源核验当前卡片。'})
    state = {'session_id': 'm2-test', 'subject_id': 'a', 'user_message_id': 101,
        'current_module': 'module_2', 'extracted_intent': 'module_2', 'memory': {}}
    update_result = await prepare_pa_reply_node(state, SimpleNamespace(context=context))
    assert provider.calls == 2
    assert update_result['pa_operations_completed'] and not update_result['pa_background_pending']
    assert update_result['current_module'] == 'module_2'
    display = update_result['pa_operation_displays'][0]
    async with card_db() as db:
        card = dict((await db.execute(select(schema.tables['goal_card_workspaces']))).mappings().one())
        runtime_state = dict((await db.execute(select(schema.tables['conversation_runtime_states']))).mappings().one())
        assert card['phase'] == 'ready' and card['display_assistant_message_id'] is None
        unavailable = await confirmation_status(db, await db.get(Conversation, 1), runtime_state, card)
        assert not unavailable['allowed']  # not yet shown to the user
        event = (await db.execute(select(AIExecutionEvent))).scalar_one()
        assert event.event_metadata['pa_background_tools']['timing'] == 'before_main_reply'
        await db.execute(insert(ConversationMessage), {'id': 102, 'conversation_id': 1,
            'position': 1, 'role': 'assistant', 'content': '一起核对这份安排。\n\n' + display})
        await finalize_tool_display(db, session_id='m2-test', user_id='a', assistant_message_id=102)
        await finalize_ui_display(db, session_id='m2-test', user_id='a', assistant_message_id=102)
        await db.commit()
    result = await confirmed(card_db)
    assert result['status'] == 'confirmed' and result['module'] == 'module_3'


@pytest.mark.asyncio
async def test_pre_reply_provider_failure_keeps_truthful_state(card_db, monkeypatch):
    from app import pa_background
    from app.pa_reply_preparation import prepare_pa_reply_node
    from app.reply_workflow import read_reply_workflow
    async with card_db() as db:
        await db.run_sync(lambda session: AIExecutionEvent.__table__.create(session.connection()))
        await db.commit()
    def unavailable(_):
        raise RuntimeError('synthetic provider failure')
    monkeypatch.setattr(pa_background, 'background_provider', unavailable)
    settings = Settings(_env_file=None, database_schema_version='v2', goal_card_ui_enabled=True)
    from app import config
    monkeypatch.setattr(config, 'get_settings', lambda: settings)
    context = SimpleNamespace(settings=settings, sessionmaker=card_db, prompt_snapshot={})
    state = {'session_id': 'm2-test', 'subject_id': 'a', 'user_message_id': 101,
        'current_module': 'module_2', 'extracted_intent': 'module_2', 'memory': {}}
    prepared = await prepare_pa_reply_node(state, SimpleNamespace(context=context))
    assert prepared['pa_operations_completed'] and not prepared['pa_background_pending']
    assert prepared['pa_operation_displays'] == []
    assert 'confirmation_receipt' not in prepared
    assert prepared['current_module'] == 'module_2'
    authority = await read_reply_workflow(card_db, 'a', 'm2-test')
    assert not authority['plan_confirmed']
    assert authority['goal_card_operation']['status'] != 'running'
    assert authority['goal_card_operation']['previous_attempt']['reason'] == 'card_preparation_failed'
