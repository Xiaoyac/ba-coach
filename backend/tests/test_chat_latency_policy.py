from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest

from app.config import Settings
from app.generation_policy import is_simple_ack, main_thinking_options
from app.retrieval_intent import decide_retrieval
from app.schemas import Message
from app.providers.deepseek import DeepSeekProvider
from app.providers.doubao import DoubaoProvider


@pytest.mark.parametrize('body', ['好的', ' 好的！ ', '可以', '我愿意', '明白了', 'OK', '你好'])
def test_exact_ack_uses_lightweight_generation(body):
    settings = Settings(_env_file=None)
    assert main_thinking_options(settings,'deepseek',[Message(role='user',content=body)]) == {'enable_thinking': False}


@pytest.mark.parametrize('body', ['好的，但我不想活了', '好的？', '不好', '不同意', '你说“好的”是什么意思',
                                 '可以但我有胸痛', '我刚完成今天的散步', '我不知道怎么办', '好'*200])
def test_unknown_negated_risk_or_substantive_is_not_an_ack(body):
    assert not is_simple_ack(body)
    assert main_thinking_options(Settings(_env_file=None),'deepseek',[Message(role='user',content=body)]) == {
        'enable_thinking': True, 'thinking_budget': 1024}


def test_rollback_and_no_shared_mutation():
    cfg = Settings(_env_file=None,chat_fast_ack_enabled=False,deepseek_reasoning_effort='provider_default')
    assert main_thinking_options(cfg,'deepseek',[Message(role='user',content='好的')]) == {
        'enable_thinking': True, 'thinking_budget': 1024}
    cfg = Settings(_env_file=None)
    simple = main_thinking_options(cfg,'deepseek',[Message(role='user',content='好的')])
    complex_options = main_thinking_options(cfg,'deepseek',[Message(role='user',content='我有个困难')])
    assert simple['enable_thinking'] is False and complex_options['enable_thinking'] is True
    assert cfg.chat_fast_ack_enabled and cfg.deepseek_reasoning_effort == 'provider_default'


@pytest.mark.parametrize('provider_name', ['deepseek', 'doubao'])
def test_substantive_reply_respects_configured_effort(provider_name):
    kwargs = {f'{provider_name}_reasoning_effort': 'high'}
    if provider_name == 'deepseek':
        kwargs.update(deepseek_model='deepseek-chat', deepseek_base_url='https://api.deepseek.com')
        expected = {'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high'}
        ack_expected = {'thinking': {'type': 'disabled'}}
    else:
        expected = {'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high'}
        ack_expected = {'thinking': {'type': 'disabled'}}
    cfg = Settings(_env_file=None, **kwargs)
    assert main_thinking_options(cfg, provider_name, [Message(role='user', content='最近做活动很困难')]) == expected
    assert main_thinking_options(cfg, provider_name, [Message(role='user', content='好的')]) == ack_expected


def test_empty_messages_or_assistant_last_does_not_trigger_fast_ack():
    cfg = Settings(_env_file=None)
    for messages in ([], [Message(role='assistant', content='好的')]):
            assert main_thinking_options(cfg, 'deepseek', messages) == {
                'enable_thinking': True, 'thinking_budget': 1024}


@pytest.mark.parametrize('previous', ['你愿意按这个安排试试吗？', '这份总结符合你的情况吗？',
                                    '你愿意进入目标设定的讨论吗？', '这个记录方式你同意吗？'])
def test_known_confirmation_skips_only_retrieval(previous):
    state = {'user_input':'好的', 'memory':{'last_user_message':'充足上下文'},
             'clinical_context':['不可省略的运动限制'], 'chat_history':[Message(role='assistant',content=previous)]}
    gate = decide_retrieval(state)
    assert not gate.retrieve and gate.reason == 'dialogue_confirmation'
    assert state['clinical_context'] == ['不可省略的运动限制']


@pytest.mark.parametrize('previous', ['你愿意听我解释行为激活的原理吗？', '你愿意了解两分钟规则吗？',
                                    '我们来聊聊那个困难？', '你愿意按这个安排试试吗？要听我解释为什么吗？',
                                    '这个计划的理论依据，你愿意听听吗？', '你愿意了解计划里的应对策略吗？'])
def test_explanation_or_unknown_ack_context_keeps_retrieval(previous):
    assert decide_retrieval({'user_input':'好的','chat_history':[Message(role='assistant',content=previous)]}).retrieve


def test_disabled_gate_restores_retrieval_even_for_confirmation():
    assert decide_retrieval({'user_input':'好的','chat_history':[Message(role='assistant',content='你愿意按这个安排试试吗？')]},enabled=False).retrieve


@pytest.mark.parametrize('provider_type', [DeepSeekProvider,DoubaoProvider])
@pytest.mark.parametrize('stream', [False,True])
@pytest.mark.parametrize('latest', ['好的','最近做活动很困难'])
async def test_same_full_prompt_and_history_one_request(provider_type,stream,latest):
    provider = object.__new__(provider_type)
    provider.model='synthetic'
    provider._settings=Settings(_env_file=None)
    result = SimpleNamespace(model='synthetic',usage=None,choices=[SimpleNamespace(
        message=SimpleNamespace(content='安全回复',reasoning_content=''),finish_reason='stop')])
    class FakeStream:
        closed = False
        async def __aiter__(self):
            yield SimpleNamespace(usage=None,choices=[SimpleNamespace(finish_reason='stop',
                delta=SimpleNamespace(content='安全回复',reasoning_content=None))])
        async def close(self): self.closed=True
    fake = FakeStream()
    create = AsyncMock(return_value=fake if stream else result)
    provider._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    messages=[Message(role='user',content='身体限制不可省略'),Message(role='assistant',content='完整计划和确认问题'),Message(role='user',content=latest)]
    if stream:
        parts=[d async for d in provider.stream(system='完整契约',messages=messages)]
        assert fake.closed and ''.join(d.text for d in parts if d.kind=='content') == '安全回复'
    else:
        assert (await provider.complete(system='完整契约',messages=messages)).text == '安全回复'
    create.assert_awaited_once()
    payload=create.call_args.kwargs
    assert payload['messages'] == [{'role':'system','content':'完整契约'}] + [
        {'role': m.role, 'content': m.content} for m in messages
    ]
    if provider_type is DeepSeekProvider:
        assert payload['extra_body'] == ({'enable_thinking': False} if latest == '好的' else {
            'enable_thinking': True, 'thinking_budget': 1024})
    else:
        assert payload['extra_body']['thinking']['type'] == ('disabled' if latest == '好的' else 'enabled')


async def test_qwen_reasoning_alias_is_copied_into_the_thinking_channel():
    provider = object.__new__(DeepSeekProvider)
    provider.model = 'qwen-max'
    provider._settings = Settings(_env_file=None)
    response = SimpleNamespace(
        model='qwen-max', usage=None,
        choices=[SimpleNamespace(
            message=SimpleNamespace(content='可见答复', reasoning_content=None, reasoning='Qwen 原生思考'),
            finish_reason='stop')],
    )
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=response)))
    )

    result = await provider.complete(
        system='系统约束', messages=[Message(role='user', content='请分析后回答')]
    )

    assert result.text == '可见答复'
    assert result.reasoning_content == 'Qwen 原生思考'
    assert result.model == 'qwen-max'


@pytest.mark.parametrize('provider_type',[DeepSeekProvider,DoubaoProvider])
async def test_module_router_keeps_reasoning_with_lower_budget(provider_type):
    provider=object.__new__(provider_type)
    provider.model='synthetic'
    provider._settings=Settings(_env_file=None,doubao_router_model='synthetic',module_router_reasoning_effort='low')
    create=AsyncMock(return_value=SimpleNamespace(model='synthetic',usage=None,choices=[SimpleNamespace(
        message=SimpleNamespace(content='{"target_module":"1"}',reasoning_content='native'),finish_reason='stop')]))
    provider._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider._client.with_options=Mock(return_value=provider._client)
    result=await provider.route_with_reasoning(system='规则',user='合成上下文')
    assert result.reasoning_content == 'native'
    if provider_type is DeepSeekProvider:
        assert 'reasoning_effort' not in create.call_args.kwargs
        assert create.call_args.kwargs['extra_body'] == {'enable_thinking': True, 'thinking_budget': 1024}
    else:
        assert create.call_args.kwargs['reasoning_effort']=='low'
        assert create.call_args.kwargs['extra_body']['thinking']['type']=='enabled'
    provider._client.with_options.assert_called_once_with(max_retries=0)


@pytest.mark.parametrize('provider_type', [DeepSeekProvider, DoubaoProvider])
@pytest.mark.parametrize('explicit_effort', [None, 'high', 'provider_default'])
async def test_mediator_explicit_effort_overrides_router_default(provider_type, explicit_effort):
    provider = object.__new__(provider_type)
    provider.model = 'synthetic'
    provider._settings = Settings(_env_file=None, doubao_router_model='synthetic')
    create = AsyncMock(return_value=SimpleNamespace(model='synthetic', usage=None, choices=[SimpleNamespace(
        message=SimpleNamespace(content='{}', reasoning_content='native'), finish_reason='stop')]))
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider._client.with_options = Mock(return_value=provider._client)
    await provider.route_with_reasoning(system='规则', user='合成上下文', reasoning_effort=explicit_effort)
    payload = create.call_args.kwargs
    if provider_type is DeepSeekProvider:
        assert 'reasoning_effort' not in payload
        assert payload['extra_body'] == {'enable_thinking': True, 'thinking_budget': 1024}
    elif explicit_effort in (None, 'provider_default'):
        assert 'reasoning_effort' not in payload
        provider._client.with_options.assert_not_called()
    else:
        assert payload['reasoning_effort'] == explicit_effort
    if provider_type is not DeepSeekProvider:
        assert payload['extra_body']['thinking']['type'] == 'enabled'


@pytest.mark.parametrize('latest', ['好的', '好的，但我不想活了'])
async def test_ack_policy_never_bypasses_risk_gate(context, provider, latest):
    from app.graph.nodes import risk_gate_node, route_after_risk
    provider.risk_result = '{"risk_status": 1, "risk_expression_type": 2}'
    update = await risk_gate_node({'user_input': latest}, SimpleNamespace(context=context))
    assert len(provider.route_calls) == 1
    assert update['risk']['risk_status'] == 1
    assert route_after_risk({**update, 'extracted_intent': 'module_2'}) == 'crisis'


@pytest.mark.parametrize('module_name', ['module_1', 'module_2', 'module_3', 'module_4'])
async def test_confirmation_still_runs_generation_and_validator(context,provider,module_name):
    from app.graph.nodes import MODULE_NODES
    kb=SimpleNamespace(search=AsyncMock(side_effect=AssertionError('unneeded retrieval')))
    state={'user_input':'好的','chat_history':[Message(role='assistant',content='你愿意按这个安排试试吗？')],
           'profile_context':['不要负重'], 'clinical_context':['计划尚未确认']}
    result=await MODULE_NODES[module_name](state,SimpleNamespace(context=replace(context,knowledge_base=kb)))
    assert result['final_response'] and len(provider.seen)==1
    assert result['telemetry']['answer_validator']['status']=='passed'
    assert result['telemetry']['knowledge_mediator']['status']=='skipped'
    assert result['telemetry']['retrieval']['gate']['reason']=='dialogue_confirmation'
    kb.search.assert_not_awaited()
    from app.providers.base import as_text
    assert '不要负重' in as_text(provider.systems[0]) and '计划尚未确认' in as_text(provider.systems[0])
