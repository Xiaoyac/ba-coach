from dataclasses import replace
from types import SimpleNamespace
import asyncio
import pytest
from app.graph import nodes
from app.providers.base import Completion, StreamDelta, as_text
from app.provisional_reply import normalize_lead
from app.reply_integrity import verify_reply


@pytest.mark.parametrize('stream', [False, True])
async def test_parallel_router_non_tool_recovers_before_any_false_delta(context, provider, monkeypatch, stream):
    calls, events = [], []
    async def authority(*args):
        return {'available': True, 'current_module': 'module_2', 'plan_confirmed': False}
    async def original_stream(**kwargs):
        yield StreamDelta(kind='content', text='我已经帮你')
        assert not events, 'no prefix of an unverified receipt may leave the server'
        yield StreamDelta(kind='content', text='记下了。')
    async def complete(**kwargs):
        calls.append(kwargs)
        if not stream and len(calls) == 1:
            return Completion(text='我已经帮你记下了。', model='fake')
        assert '尚未展示' in as_text(kwargs['system'])
        assert len(kwargs['messages']) == 1
        return Completion(text='目前还未正式确认这份计划，已有安排不需要重新说明。', model='fake')
    monkeypatch.setattr('app.reply_workflow.read_reply_workflow', authority)
    monkeypatch.setattr(provider, 'stream', original_stream)
    monkeypatch.setattr(provider, 'complete', complete)
    ctx = replace(context, stream=stream, settings=context.settings.model_copy(update={
        'database_schema_version': 'v2', 'pa_card_tools_enabled': False, 'goal_card_ui_enabled': False}))
    result = await nodes.make_module_node('module_2', nodes.ModuleConfig(retrieve=False))({
        'session_id': 'synthetic', 'subject_id': 'owner', 'user_input': '请保存到我的目标',
        'routing_mode': 'router_only', 'telemetry': {'reply_mode': 'ack_deep'}, 'memory': {}},
        SimpleNamespace(context=ctx), writer=lambda e: events.append(e) if e['type'] == 'delta' else None)
    assert result['error'] is None and not result['reply_held']
    assert result['telemetry']['workflow_truth']['status'] == 'recovered'
    assert result['final_response'] == '目前还未正式确认这份计划，已有安排不需要重新说明。'
    if stream:
        assert ''.join(event['text'] for event in events) == result['final_response']


@pytest.mark.parametrize('lead', ['我已经帮你记下了。', '已经记录到我的目标。', '目标已记录。'])
def test_parallel_opening_cannot_publish_a_receipt(lead):
    assert normalize_lead(lead) == ''


async def test_stop_during_save_recovery_does_not_retry_or_swallow_cancellation():
    class Provider:
        count = 0
        async def complete(self, **kwargs):
            self.count += 1
            raise asyncio.CancelledError()
    async def authority(): return {'plan_confirmed': False}
    p = Provider()
    with pytest.raises(asyncio.CancelledError):
        await verify_reply(reply='计划已保存。', read_authority=authority, module='module_2',
            provider=p, system='', messages=[], elapsed_seconds=0, total_timeout_seconds=30)
    assert p.count == 1


async def test_expired_budget_does_not_start_another_model_request():
    class Provider:
        async def complete(self, **kwargs): pytest.fail('must not spend past the existing deadline')
    async def authority(): return {'plan_confirmed': False}
    result, audit = await verify_reply(reply='目标已保存。', read_authority=authority, module='module_2',
        provider=Provider(), system='', messages=[], elapsed_seconds=30, total_timeout_seconds=30)
    assert result is None and audit['reason'] == 'budget_exhausted'


@pytest.mark.parametrize('prior_plan,receipt', [(True, False), (True, True), (False, False)])
async def test_prior_plan_is_not_a_new_write_receipt(prior_plan, receipt):
    async def authority(): return {'available': True, 'current_module': 'module_2', 'plan_confirmed': prior_plan}
    class Provider:
        count = 0
        async def complete(self, **kwargs):
            self.count += 1
            return Completion(text='本轮尚未提交新的修改。', model='fake')
    p = Provider()
    result, audit = await verify_reply(reply='计划已保存。', read_authority=authority, module='module_2',
        provider=p, system='', messages=[], elapsed_seconds=0, total_timeout_seconds=30,
        current_turn_committed=receipt)
    assert (audit['status'] == 'passed') == (prior_plan and receipt)
    assert p.count == (0 if prior_plan and receipt else 1)


@pytest.mark.parametrize('chunk_size', [1, 2, 3, 7, 50])
def test_sentence_guard_keeps_safe_streaming_but_hides_split_save_claim(chunk_size):
    from app.reply_integrity import SentenceSaveGuard
    guard = SentenceSaveGuard({'available': True, 'current_module': 'module_2', 'plan_confirmed': False}, 'module_2')
    text = '先接着谈谈这个安排。计划已保存。下一句话。'
    visible = []
    for offset in range(0, len(text), chunk_size):
        visible.extend(guard.push(text[offset:offset+chunk_size]))
    assert ''.join(visible) == '先接着谈谈这个安排。'
    assert guard.pending == '计划已保存。下一句话。'
    assert guard.blocked


async def test_recovery_never_replaces_or_repeats_an_already_displayed_prefix(context, provider, monkeypatch):
    events = []
    async def authority(*args):
        return {'available': True, 'current_module': 'module_2', 'plan_confirmed': False}
    async def stream(**kwargs):
        yield StreamDelta(kind='content', text='你想把这个安排留下来。')
        assert events and events[0]['text'] == '你想把这个安排留下来。'
        yield StreamDelta(kind='content', text='计划已保存。')
    async def complete(**kwargs):
        assert '前缀已经展示' in as_text(kwargs['system'])
        return Completion(text='这份计划尚未正式确认。', model='fake')
    monkeypatch.setattr('app.reply_workflow.read_reply_workflow', authority)
    monkeypatch.setattr(provider, 'stream', stream)
    monkeypatch.setattr(provider, 'complete', complete)
    ctx = replace(context, stream=True, settings=context.settings.model_copy(update={'database_schema_version': 'v2'}))
    result = await nodes.make_module_node('module_2', nodes.ModuleConfig(retrieve=False))({
        'session_id': 'synthetic', 'subject_id': 'owner', 'user_input': '请保存', 'memory': {},
        'routing_mode': 'router_only', 'telemetry': {'reply_mode': 'ack_deep'}},
        SimpleNamespace(context=ctx), writer=lambda e: events.append(e) if e['type'] == 'delta' else None)
    assert result['final_response'] == '你想把这个安排留下来。这份计划尚未正式确认。'
    assert ''.join(e['text'] for e in events) == result['final_response']
    assert not result['reply_held']
