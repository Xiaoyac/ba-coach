"""Regressions for failures observed with the real local provider."""
from dataclasses import replace

import pytest
from langgraph.runtime import Runtime

from app.graph import nodes
from app import reply_workflow
from app.providers.base import Completion, StreamDelta
from app.providers.base import ProviderError


def test_plan_duration_change_keeps_schedule_text_consistent():
    from app.v2_workflow import _synchronize_duration_text

    values = _synchronize_duration_text(
        {'duration_minutes': 3},
        {'duration_minutes': 5, 'schedule_text': '每天晚上在书房做五分钟，先试三天'},
    )

    assert values == {'duration_minutes': 3, 'schedule_text': '每天晚上在书房做3分钟，先试三天'}


def test_ambiguous_plan_duration_change_keeps_last_committed_pair():
    from app.v2_workflow import _synchronize_duration_text

    values = _synchronize_duration_text(
        {'duration_minutes': 3},
        {'duration_minutes': 5, 'schedule_text': '五分钟热身后再做五分钟，先试三天'},
    )

    assert values['duration_minutes'] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_review_request_renders_saved_version_after_nonproposal_reply(
    context, provider, monkeypatch, stream,
):
    import json
    from app.providers.base import Message
    card = {'text': '活动：客厅伸展\n时间：明晚八点\n时长：5分钟\n这份安排可以吗？',
            'record_id': 'existing-plan', 'record_hash': 'existing-version'}
    user = '请把现有安排整理成完整计划让我确认。'
    async def authority(*args):
        return {'available': True, 'current_module': 'module_2',
            'goal_selected': True, 'plan_confirmed': False, 'confirmation_summary': card}
    async def classify(**kwargs):
        payload = json.loads(kwargs['user'])
        assert payload['assistant_proposal'] == card['text']
        return json.dumps({'intent': 'review', 'source_quote': user,
                           'amendment': False, 'unresolved': False})
    monkeypatch.setattr(provider, 'route', classify)
    monkeypatch.setattr(reply_workflow, 'read_reply_workflow', authority)
    monkeypatch.setattr(nodes, '_emit', lambda event: None)
    ctx = replace(context, stream=stream, sessionmaker=None,
        settings=context.settings.model_copy(update={'database_schema_version': 'v2'}))
    result = await nodes.MODULE_NODES['module_2']({
        'session_id': 'redisplay-regression', 'subject_id': 'local-user',
        'user_input': user,
        'chat_history': [Message(role='assistant', content='计划的讨论和确认都在这里完成。')],
    }, Runtime(context=ctx))
    assert result['final_response'] == card['text']
    assert result['telemetry']['rendered_confirmation'] == {
        'record_id': card['record_id'], 'record_hash': card['record_hash']}
    assert 'confirmation_receipt' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('unsafe', [False, True])
async def test_workflow_reply_correction_retains_only_safe_user_evidence(
    context, provider, monkeypatch, stream, unsafe,
):
    original = '好的，计划就这么定了。' + ('不要再找借口。' if unsafe else '')
    async def generated(**kwargs):
        return Completion(text=original, model='stub')
    async def streamed(**kwargs):
        yield StreamDelta(kind='content', text=original)
    async def authority(*args):
        return {'available': True, 'current_module': 'module_2',
                'goal_selected': False, 'plan_confirmed': False}
    monkeypatch.setattr(provider, 'complete', generated)
    monkeypatch.setattr(provider, 'stream', streamed)
    monkeypatch.setattr(reply_workflow, 'read_reply_workflow', authority)
    events = []
    monkeypatch.setattr(nodes, '_emit', events.append)
    ctx = replace(context, stream=stream, sessionmaker=None,
        settings=context.settings.model_copy(update={'database_schema_version': 'v2'}))
    result = await nodes.MODULE_NODES['module_2']({
        'session_id': 'local-regression', 'subject_id': 'local-user',
        'user_input': '我选择晚饭后散步，每天十分钟。', 'chat_history': [],
    }, Runtime(context=ctx))
    assert result['final_response'] != original
    assert result['reply_held'] is unsafe
    audit = result['telemetry']['answer_validator']
    assert audit['progression_held'] is unsafe
    assert audit['user_evidence_retained'] is (not unsafe)
    if stream:
        assert ''.join(e['text'] for e in events if e['type'] == 'delta') == result['final_response']
        assert original not in str(events)


@pytest.mark.parametrize('stream', [False, True])
def test_successful_precommit_does_not_readopt_under_turn_lock(client, monkeypatch, stream):
    from app.routes import chat
    original = chat._initial_state
    calls = []
    async def initial(*args, **kwargs):
        calls.append(True)
        assert len(calls) == 1, 're-adopt would re-enter the non-reentrant turn lock'
        return await original(*args, **kwargs)
    async def precommit(state, context, **kwargs):
        store, session_id = context.store, state['session_id']
        lock = await store.get_turn_lock(session_id)
        assert lock.locked()
        await store.set_module(session_id, 'module_3')
        await store.set_memory(session_id, {})
        return {'current_module': 'module_3', 'extracted_intent': 'module_3',
                'next_module': 'module_3', 'routed_by': 'pre_reply_router'}
    monkeypatch.setattr(chat, '_initial_state', initial)
    monkeypatch.setattr('app.pre_reply_routing.route_before_reply', precommit)
    response = client.post('/api/chat/stream' if stream else '/api/chat',
        json={'message': '我同意这份计划。'})
    assert response.status_code == 200
    assert len(calls) == 1
    if not stream:
        assert response.json()['reply_module'] == 'module_3'


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('module', ['module_3', 'module_4'])
async def test_provider_failure_uses_only_a_real_confirmation_receipt(
    context, provider, monkeypatch, stream, committed, module,
):
    async def failed_complete(**kwargs):
        raise ProviderError('upstream model failed')

    async def failed_stream(**kwargs):
        yield StreamDelta(kind='content', text='未完成的故障草稿')
        raise ProviderError('upstream model failed')

    async def authority(*args, **kwargs):
        return {'available': True, 'current_module': module,
                'goal_selected': True, 'plan_confirmed': True}

    monkeypatch.setattr(provider, 'complete', failed_complete)
    monkeypatch.setattr(provider, 'stream', failed_stream)
    monkeypatch.setattr(reply_workflow, 'read_reply_workflow', authority)
    events = []
    monkeypatch.setattr(nodes, '_emit', events.append)
    ctx = replace(context, stream=stream, sessionmaker=None,
        settings=context.settings.model_copy(update={'database_schema_version': 'v2'}))
    state = {'session_id': 'receipt-regression', 'subject_id': 'local-user',
             'user_input': '我确认这份安排。', 'chat_history': []}
    if committed:
        state['confirmation_receipt'] = {'module': module, 'cycle_id': 'committed-cycle'}
    result = await nodes.MODULE_NODES[module](state, Runtime(context=ctx))
    if committed:
        expected = '本轮确认已保存，但后续回复生成中断。已有对话和记录已保留。'
        assert result['final_response'] == expected
        assert result['error'] is None
        assert result['telemetry']['confirmation_receipt_recovery'] == 'provider_error'
        if stream:
            assert ''.join(e['text'] for e in events if e['type'] == 'delta') == expected
            assert '未完成的故障草稿' not in str(events)
    else:
        assert result['error'] == 'upstream model failed'
        assert '已确认并保存' not in result['final_response']
        assert 'confirmation_receipt_recovery' not in result['telemetry']
