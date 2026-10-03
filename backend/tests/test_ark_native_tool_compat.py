"""Exercise serialized tool requests, including the real PA loop boundary."""
import json
import openai
import pytest
from openai import _base_client
from app.config import Settings
from app.providers.deepseek import DeepSeekProvider
from app.providers.base import ProviderError
from app.pa_tool_loop import PAToolReply
from app.schemas import Message

http = getattr(_base_client, 'httpx2', None) or _base_client.httpx
ARK = 'https://ark.cn-beijing.volces.com/api/coding/v3'


def tool(name):
    return {'type': 'function', 'function': {'name': name, 'parameters': {
        'type': 'object', 'properties': {}, 'required': []}}}


def response(name=None):
    delta = {'content': None if name else '继续讨论。', 'reasoning_content': 'synthetic reasoning'}
    if name:
        delta['tool_calls'] = [{'index': 0, 'id': 'call-' + name, 'type': 'function',
            'function': {'name': name, 'arguments': '{}'}}]
    event = {'id': 'completion', 'object': 'chat.completion.chunk', 'created': 1,
        'model': 'kimi-k3', 'choices': [{'index': 0, 'delta': delta,
            'finish_reason': 'tool_calls' if name else 'stop'}]}
    return http.Response(200, headers={'content-type': 'text/event-stream', 'x-request-id': 'test-id'},
        content=('data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n').encode())


def provider(handler, url=ARK):
    p = object.__new__(DeepSeekProvider)
    p.model = 'kimi-k3'
    p._settings = Settings(_env_file=None, deepseek_api_key='test', deepseek_model=p.model,
                          deepseek_base_url=url, deepseek_max_tokens=4000, provider_request_timeout_seconds=60)
    p._client = openai.AsyncOpenAI(api_key='test', base_url=url, max_retries=0,
        http_client=http.AsyncClient(transport=http.MockTransport(handler)))
    return p


@pytest.mark.parametrize('effort', ['low', 'high', 'max'])
async def test_named_read_required_decision_and_final_reply_keep_deep_policy(effort):
    seen = []
    def handler(request):
        body = json.loads(request.content); seen.append(body)
        assert body['reasoning_effort'] == effort
        assert body['max_tokens'] >= 16384
        assert request.extensions['timeout']['read'] >= 180
        assert not {'thinking', 'enable_thinking'} & body.keys()
        if len(seen) == 1:
            assert body['tool_choice'] == 'required'
            assert [t['function']['name'] for t in body['tools']] == ['get_pa_card']
            return response('get_pa_card')
        assert body['messages'][-1]['role'] == 'tool'
        assert body['messages'][-2]['reasoning_content'] == 'synthetic reasoning'
        if len(seen) == 2:
            assert body['tool_choice'] == 'required'
            assert [t['function']['name'] for t in body['tools']] == ['continue_pa_conversation']
            return response('continue_pa_conversation')
        assert body['tool_choice'] == 'none'
        return response()
    class Executor:
        definitions = [tool('get_pa_card'), tool('continue_pa_conversation')]
        trace = []; displays = []
        async def execute(self, call):
            name = call['function']['name']
            return {'status': 'ok' if name == 'get_pa_card' else 'continue_conversation'}
    p = provider(handler).with_deep_reply(effort)
    try:
        loop = PAToolReply(p, Executor(), max_rounds=3, telemetry={})
        out = [d async for d in loop.stream(system='synthetic', messages=[Message(role='user', content='继续')])]
        assert ''.join(d.text for d in out if d.kind == 'content') == '继续讨论。'
        assert len(seen) == 3
        assert len(Executor.definitions) == 2
    finally:
        await p._client.close()


@pytest.mark.parametrize('offered,returned', [('missing', 'get_pa_card'), ('get_pa_card', 'save_pa_card')])
async def test_named_constraint_cannot_execute_missing_or_different_tool(offered, returned):
    seen = []
    def handler(request):
        seen.append(request); return response(returned)
    p = provider(handler)
    try:
        with pytest.raises(ProviderError):
            async for delta in p.stream_tools(system='test', messages=[{'role': 'user', 'content': 'test'}],
                    tools=[tool(offered)], tool_choice={'type': 'function', 'function': {'name': 'get_pa_card'}}):
                assert delta.kind != 'tool_calls'
        assert len(seen) == (0 if offered == 'missing' else 1)
    finally:
        await p._client.close()


async def test_other_endpoints_retain_native_named_choice():
    choice = {'type': 'function', 'function': {'name': 'get_pa_card'}}
    definitions = [tool('get_pa_card'), tool('save_pa_card')]
    def handler(request):
        body = json.loads(request.content)
        assert body['tool_choice'] == choice and body['tools'] == definitions
        return response('get_pa_card')
    p = provider(handler, 'https://api.moonshot.cn/v1')
    try:
        out = [d async for d in p.stream_tools(system='test', messages=[{'role': 'user', 'content': 'test'}],
                                             tools=definitions, tool_choice=choice)]
        assert out[-1].tool_calls[0]['function']['name'] == 'get_pa_card'
    finally:
        await p._client.close()
