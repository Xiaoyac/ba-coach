import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import hybrid_mediator
from app.config import Settings
from app.graph import get_graph
from app.providers.base import Completion, as_text
from app.retrieval import KnowledgeChunk
from test_admin_sandbox import sandbox_admin_headers

RAW = json.dumps({"decision": "use", "selections": [
    {"id": "one", "quote": "行动可能帮助情绪", "application": "说明行动与心情关系，不保证立即改善"}], "note": ""}, ensure_ascii=False)
CHUNKS = [KnowledgeChunk("one", "行动可能帮助情绪。", "BA")]


def stub(monkeypatch, text=RAW, finish="stop"):
    provider = SimpleNamespace(name="deepseek", model="deepseek-v4.1-flash",
        _client=SimpleNamespace(close=AsyncMock()),
        route_detailed=AsyncMock(return_value=Completion(text=text,
            reasoning_content="PROVIDER_THOUGHT", model="deepseek-v4.1-flash",
            finish_reason=finish, request_id="mediator-request")))
    factory = lambda settings: provider
    monkeypatch.setattr(hybrid_mediator, "build_provider", factory)
    return provider


async def test_hybrid_graph_calls_mediator_and_persists_separate_debug(context, provider, monkeypatch):
    mediator = stub(monkeypatch)
    class KB:
        async def search(self, **kwargs):
            return CHUNKS
    context = replace(context, knowledge_base=KB(), knowledge_mediator_bypass=True,
        prompt_snapshot={"knowledge_mediator": "ADMIN_SAVED_MEDIATOR"})
    context.settings.knowledge_intent_gate_enabled = False
    context.settings.knowledge_mediator_enabled = False  # legacy switch cannot disable hybrid mediation
    context.router_provider = context.router_provider.with_thinking(True)
    result = await get_graph().ainvoke({"user_input": "行动为什么有用？", "forced_module": "module_2"}, context=context)
    call = mediator.route_detailed.call_args.kwargs
    assert call["include_reasoning"] is False and call["reasoning_effort"] is None
    assert call["max_tokens"] == 512 and "ADMIN_SAVED_MEDIATOR" in call["system"]
    metrics = result["telemetry"]["knowledge_mediator"]
    assert metrics["status"] == "completed" and metrics["effective_thinking"] == "disabled"
    assert metrics["request_id"] == "mediator-request"
    assert "PROVIDER_THOUGHT" not in str(metrics)
    snapshot = result["telemetry"]["knowledge_references"]
    assert snapshot["mediator_response_content"] == RAW
    assert snapshot["mediator_reasoning_content"] == "PROVIDER_THOUGHT"
    assert snapshot["provided"][0]["id"] == "one"
    prompt = as_text(provider.systems[-1])
    assert "说明行动与心情关系" in prompt and "PROVIDER_THOUGHT" not in prompt
    mediator._client.close.assert_awaited_once()


@pytest.mark.parametrize("text,finish", [("bad json", "stop"), (RAW, "length"),
    (RAW.replace('"one"', '"foreign"'), "stop")])
async def test_invalid_return_visible_only_in_debug(monkeypatch, text, finish):
    mediator = stub(monkeypatch, text, finish)
    debug = {}
    chunks, block, metrics = await hybrid_mediator.mediate_hybrid_knowledge(
        settings=Settings(_env_file=None), state={"user_input": "为什么"},
        module="module_2", knowledge=CHUNKS, debug_output=debug)
    assert chunks == [] and block == "" and metrics["withheld_on_error"]
    assert debug["response_content"] == text
    mediator._client.close.assert_awaited_once()


async def test_deadline_cancels_without_retry_or_raw_fallback(monkeypatch):
    mediator = stub(monkeypatch)
    cancelled = asyncio.Event()
    async def slow(**kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
    mediator.route_detailed.side_effect = slow
    settings = Settings(_env_file=None).model_copy(update={"knowledge_mediator_timeout_seconds": .01})
    chunks, block, metrics = await hybrid_mediator.mediate_hybrid_knowledge(
        settings=settings, state={"user_input": "为什么"}, module="module_2", knowledge=CHUNKS)
    assert chunks == [] and block == "" and metrics["reason"] == "timeout"
    assert cancelled.is_set() and mediator.route_detailed.await_count == 1
    mediator._client.close.assert_awaited_once()


@pytest.mark.parametrize("knowledge,state,module", [([], {"user_input": "你好"}, "module_1"),
    (CHUNKS, {"recording_status": "declined"}, "module_3")])
async def test_empty_and_declined_do_not_construct_client(monkeypatch, knowledge, state, module):
    def forbidden(settings):
        pytest.fail("must not construct provider")
    monkeypatch.setattr(hybrid_mediator, "build_provider", forbidden)
    chunks, block, metrics = await hybrid_mediator.mediate_hybrid_knowledge(
        settings=Settings(_env_file=None), state=state, module=module, knowledge=knowledge)
    assert not chunks and not block and metrics["status"] != "fallback"


@pytest.mark.parametrize("stream", [False, True])
def test_hybrid_debug_is_saved_per_message_and_owned(client, sandbox_admin_headers, register,
        monkeypatch, caplog, stream):
    from app import retrieval
    from app.config import get_settings
    mediator = stub(monkeypatch)
    class KB:
        async def search(self, **kwargs):
            return CHUNKS
    monkeypatch.setattr(retrieval, "get_hybrid_knowledge_base", KB)
    get_settings().knowledge_intent_gate_enabled = False
    sid = client.post('/api/conversations', headers=sandbox_admin_headers, json={}).json()['session_id']
    assert client.patch(f'/api/conversations/{sid}/knowledge-mediator', headers=sandbox_admin_headers,
        json={'enabled': False}).status_code == 200
    assert client.patch(f'/api/conversations/{sid}/mediator-node', headers=sandbox_admin_headers,
        json={'enabled': True}).status_code == 200
    response = client.post('/api/chat/stream' if stream else '/api/chat', headers=sandbox_admin_headers,
        json={'session_id': sid, 'message': '行动为什么有用？'})
    assert response.status_code == 200
    message = client.get(f'/api/conversations/{sid}', headers=sandbox_admin_headers).json()['messages'][-1]
    url = f'/api/conversations/messages/{message["id"]}/knowledge'
    saved = client.get(url, headers=sandbox_admin_headers).json()
    assert saved['mediator_response_content'] == RAW
    assert saved['mediator_reasoning_content'] == 'PROVIDER_THOUGHT'
    assert 'PROVIDER_THOUGHT' not in caplog.text
    calls = mediator.route_detailed.await_count
    assert client.get(url, headers=sandbox_admin_headers).json() == saved
    assert mediator.route_detailed.await_count == calls  # no regeneration on expand
    assert client.get(url).status_code == 401
    assert client.get(url, headers=register(username='hybridother')).status_code == 404


@pytest.mark.parametrize("model,expected", [("deepseek-v4.1-flash", {"thinking": {"type": "disabled"}}),
    ("kimi-k3", {"reasoning_effort": "low"})])
async def test_actual_provider_wire_disables_thinking_or_uses_low(monkeypatch, model, expected):
    import httpx
    settings = Settings(_env_file=None, deepseek_api_key="synthetic", deepseek_base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        knowledge_hybrid_mediator_model=model, provider_max_retries=2, knowledge_mediator_timeout_seconds=60)
    p = hybrid_mediator.build_provider(settings)
    original = p._client
    payloads = []
    def response(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "synthetic", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": RAW}}]})
    import openai
    p._client = openai.AsyncOpenAI(api_key="synthetic", base_url=settings.deepseek_base_url,
        max_retries=p._settings.provider_max_retries, http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)))
    try:
        await p.route_detailed(system="test", user="test", max_tokens=512, include_reasoning=False)
        assert len(payloads) == 1
        assert all(payloads[0][key] == value for key, value in expected.items())
        assert payloads[0]["model"] == model
        assert p.thinking_override is False and p._client.max_retries == 0
        assert p._settings.router_request_timeout_seconds == 8
        assert settings.provider_max_retries == 2  # no mutation of shared settings
    finally:
        await p._client.close()
        await original.close()
