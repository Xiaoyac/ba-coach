"""Skipped openings remain safe and expose actionable, content-free reasons."""
import pytest
from app.provisional_reply import classify_lead, generate_reply_lead
from app.providers.base import Completion

@pytest.mark.parametrize('raw,reason', [
    ('', 'empty_output'), ('甲' * 151, 'too_long'), ('{"reply":"你好"}', 'structured_output'),
    ('你愿意吗？', 'question'), ('一。二。三。', 'too_many_sentences'),
    ('我已经帮你记下了。', 'uncommitted_save_claim'),
    ('进入下一个模块。', 'stage_transition'), ('你说得对。', 'unconditional_agreement'),
    ('正在思考。', 'placeholder'), ('收到。', 'placeholder'),
])
def test_filter_reasons_preserve_rejections(raw, reason):
    assert classify_lead(raw) == ('', reason)

class Provider:
    name = model = 'stub'
    def with_thinking(self, enabled): return self
    async def complete(self, **kwargs):
        return Completion(text=self.text, model='stub', finish_reason=self.finish)

@pytest.mark.asyncio
@pytest.mark.parametrize('text,finish,reason', [
    ('你愿意吗？', 'stop', 'question'), ('', 'stop', 'empty_output'),
    ('可接受但尚未结束。', 'length', 'incomplete_generation'),
])
async def test_metrics_explain_rejection_without_candidate_content(text, finish, reason):
    p = Provider(); p.text = text; p.finish = finish
    result = await generate_reply_lead(p, user_input='测试', history=[])
    assert result['reason_code'] == 'no_suitable_lead'
    assert result['rejection_reason'] == reason
    assert result['text'] == '' and result['displayed'] is False
    assert 'raw_text' not in result and 'generated_text' not in result

@pytest.mark.parametrize('length,accepted', [(150, True), (151, False)])
def test_150_character_boundary(length, accepted):
    raw = '甲' * (length - 1) + '。'
    text, reason = classify_lead(raw)
    assert bool(text) is accepted
    assert (reason is None) is accepted

def test_added_punctuation_counts_toward_limit():
    assert len(classify_lead('甲' * 149)[0]) == 150
    assert classify_lead('甲' * 150) == ('', 'too_long')

@pytest.mark.asyncio
async def test_lead_starts_while_real_router_is_still_pending(context, provider, monkeypatch):
    import asyncio
    from dataclasses import replace
    from app.graph import builder
    from app.provisional_reply import with_natural_lead
    from app.providers.base import StreamDelta
    entered = asyncio.Event(); router_done = asyncio.Event()
    original_router = builder.pre_reply_router_node
    async def slow_router(state, runtime):
        await asyncio.wait_for(entered.wait(), 1)
        router_done.set()
        return await original_router(state, runtime)
    async def opening(**kwargs):
        assert not router_done.is_set()
        entered.set()
        return Completion(text='你在认真考虑这件事。', model='stub', finish_reason='stop')
    async def body(**kwargs):
        assert router_done.is_set()
        yield StreamDelta(kind='content', text='接着谈谈。')
    monkeypatch.setattr(builder, 'pre_reply_router_node', slow_router)
    monkeypatch.setattr(provider, 'complete', opening)
    monkeypatch.setattr(provider, 'stream', body)
    ctx = replace(context, stream=True)
    state = {'user_input':'昨天太累没去散步', 'telemetry':{'reply_mode':'ack_deep'}}
    graph = builder.build_graph().compile()
    metrics = {}
    output = [item async for item in with_natural_lead(
        graph.astream(state, context=ctx, stream_mode=['custom','values']),
        provider=provider, user_input=state['user_input'], generation_id='g',
        session_id='s', subject_id=None, maker=None, metrics=metrics, context=ctx)]
    assert entered.is_set() and router_done.is_set()
    assert metrics['context_wait_ms'] >= 0
    assert ''.join(e.get('text','') for mode,e in output if mode=='custom' and e.get('type')=='delta') == '你在认真考虑这件事。\n\n接着谈谈。'

@pytest.mark.asyncio
@pytest.mark.parametrize('flagged', [True, False])
async def test_risk_gate_precedes_lead_and_diversion_skips_it(flagged):
    from types import SimpleNamespace
    from app.provisional_reply import with_natural_lead
    called = []
    class Lead(Provider):
        async def complete(self, **kwargs):
            called.append(True)
            return Completion(text='我听到了。', model='stub', finish_reason='stop')
    ctx = SimpleNamespace(settings=SimpleNamespace(risk_gate_enabled=True), reply_lead_context=None)
    metrics={}
    async def events():
        yield ('values', {'chat_history':[], 'telemetry':{'history_source':'session'}})
        assert not called
        yield ('values', {'chat_history':[], 'risk': {'risk_status':True} if flagged else None,
                         'telemetry':{'history_source':'session','risk_gate_duration_ms':10}})
        await ctx.reply_lead_task
        yield ('custom', {'type':'delta','text':'危机支持' if flagged else '正文'})
    output = [e async for e in with_natural_lead(events(),provider=Lead(),user_input='测试',
        generation_id='g',session_id='s',subject_id=None,maker=None,metrics=metrics,context=ctx)]
    assert bool(called) is not flagged
    if flagged:
        assert metrics['reason_code']=='risk_gate_diverted'
        assert not any(e.get('phase')=='lead' for _,e in output)

@pytest.mark.asyncio
async def test_legacy_qwen_configuration_reports_error_without_calling_it():
    from app.config import Settings
    from app.providers.deepseek import DeepSeekProvider
    s=Settings(_env_file=None,deepseek_api_key='test',deepseek_model='kimi-k3',
        deepseek_base_url='https://ark.cn-beijing.volces.com/api/coding/v3',
        reply_lead_model='qwen3.8-flash',reply_lead_base_url='https://example.invalid',reply_lead_api_key='test')
    provider=DeepSeekProvider(s)
    try:
        result=await generate_reply_lead(provider,user_input='测试',history=[],settings=s)
        assert result['reason_code']=='lead_provider_error' and result['error_type']=='ValueError'
        assert result['text']==''
    finally:
        await provider._client.close()


@pytest.mark.asyncio
async def test_existing_ark_provider_is_reused_without_a_new_secret_channel():
    from app.config import Settings
    from app.providers.deepseek import DeepSeekProvider
    from app.provisional_reply import select_lead_provider
    s = Settings(_env_file=None, deepseek_api_key='synthetic-test-only', deepseek_model='kimi-k3',
        deepseek_base_url='https://ark.cn-beijing.volces.com/api/coding/v3',
        reply_lead_provider='deepseek')
    provider = DeepSeekProvider(s)
    try:
        assert select_lead_provider(provider, s) is provider
        assert s.reply_lead_api_key is None and s.reply_lead_base_url is None
    finally:
        await provider._client.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('dedicated', [False, True])
async def test_k3_label_on_non_ark_endpoint_is_rejected_before_request(dedicated):
    from app.config import Settings
    from app.providers.deepseek import DeepSeekProvider
    from app.provisional_reply import select_lead_provider
    s = Settings(_env_file=None, deepseek_api_key='synthetic-test-only', deepseek_model='kimi-k3',
        deepseek_base_url='https://example.invalid/v1',
        reply_lead_base_url='https://example.invalid/v1' if dedicated else None,
        reply_lead_api_key='synthetic-test-only' if dedicated else None)
    provider = DeepSeekProvider(s)
    try:
        with pytest.raises(ValueError, match='Volcengine Ark'):
            select_lead_provider(provider, s)
    finally:
        await provider._client.close()
