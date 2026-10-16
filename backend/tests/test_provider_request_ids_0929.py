"""Use the real SDK over an offline transport to verify header request IDs."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openai
import pytest
import pytest_asyncio
from openai import _base_client

from app.config import Settings
from app.providers.base import ProviderError
from app.providers.deepseek import DeepSeekProvider
from app.schemas import Message


# The supported SDK range spans its httpx -> httpx2 transport migration.
http = getattr(_base_client, "httpx2", None) or _base_client.httpx
HEADER_ID = "provider-header-request-id"
BODY_ID = "chatcmpl-unrelated-completion-id"
MESSAGES = [Message(role="user", content="offline test")]


@pytest_asyncio.fixture
async def provider_factory():
    clients = []

    def factory(handler):
        provider = object.__new__(DeepSeekProvider)
        provider.model = "qwen3.8-flash"
        provider._settings = Settings(_env_file=None, deepseek_model=provider.model,
                                      deepseek_router_model=provider.model)
        provider._client = openai.AsyncOpenAI(
            api_key="fake-offline-key", base_url="https://mock.invalid/v1", max_retries=0,
            http_client=http.AsyncClient(transport=http.MockTransport(handler)),
        )
        clients.append(provider._client)
        return provider

    yield factory
    for client in clients:
        await client.close()


def _success(request, header_id):
    payload = json.loads(request.content)
    response = {"id": BODY_ID, "created": 1, "model": "qwen3.8-flash", "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "reply"},
                             "finish_reason": "stop"}]}
    headers = {"x-request-id": header_id} if header_id else {}
    if payload.get("stream"):
        response.update(object="chat.completion.chunk", choices=[
            {"index": 0, "delta": {"role": "assistant", "content": "reply"}, "finish_reason": "stop"}])
        usage = {**response, "choices": [], "usage": {
            "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
        headers["content-type"] = "text/event-stream"
        body = "data: " + json.dumps(response) + "\n\ndata: " + json.dumps(usage) + "\n\ndata: [DONE]\n\n"
        return http.Response(200, headers=headers, content=body.encode())
    return http.Response(200, headers=headers, json=response)


async def _complete(provider, path):
    if path == "complete":
        return await provider.complete(system="test", messages=MESSAGES)
    if path == "recovery":
        return await provider.complete_without_reasoning(system="test", messages=MESSAGES)
    return await provider.route_detailed(system="test", user="offline test",
                                         include_reasoning=path == "reasoning_router")


@pytest.mark.parametrize("path", ["complete", "recovery", "router", "reasoning_router"])
@pytest.mark.parametrize("header_id", [HEADER_ID, None])
async def test_nonstream_ids_are_real_http_headers_not_completion_ids(provider_factory, path, header_id):
    provider = provider_factory(lambda request: _success(request, header_id))
    result = await _complete(provider, path)
    assert result.text == "reply"
    assert result.request_id == header_id
    assert result.request_id != BODY_ID


@pytest.mark.parametrize("header_id", [HEADER_ID, None])
async def test_stream_preserves_header_before_content_and_in_final_usage(provider_factory, header_id):
    provider = provider_factory(lambda request: _success(request, header_id))
    deltas = [delta async for delta in provider.stream(system="test", messages=MESSAGES)]
    assert "".join(delta.text for delta in deltas if delta.kind == "content") == "reply"
    assert deltas[-1].kind == "usage"
    assert deltas[-1].request_id == header_id
    assert deltas[-1].usage == {"input_tokens": 10, "output_tokens": 2, "reasoning_tokens": 0}
    if header_id:
        assert deltas[0].kind == "usage"
        assert deltas[0].usage == {}
        assert deltas[0].request_id == header_id
    else:
        assert deltas[0].kind == "content"
        assert all(delta.request_id is None for delta in deltas)


@pytest.mark.parametrize("header_id", [HEADER_ID, None])
async def test_stream_real_header_takes_priority_over_legacy_stub_attribute(header_id):
    class Stream:
        _request_id = "legacy-stub-id"
        response = SimpleNamespace(headers={"x-request-id": header_id} if header_id else {})
        close = AsyncMock()

        async def __aiter__(self):
            if False:
                yield None

    provider = object.__new__(DeepSeekProvider)
    provider.model = "qwen3.8-flash"
    provider._settings = Settings(_env_file=None)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=AsyncMock(return_value=Stream()))))
    deltas = [delta async for delta in provider.stream(system="test", messages=MESSAGES)]
    assert deltas[-1].request_id == (header_id or "legacy-stub-id")


@pytest.mark.parametrize("path", ["complete", "recovery", "router", "reasoning_router", "stream"])
@pytest.mark.parametrize("header_id", [HEADER_ID, None])
async def test_http_errors_keep_only_the_actual_header_and_do_not_add_retries(provider_factory, path, header_id):
    calls = []

    def handler(request):
        calls.append(request)
        return http.Response(503, headers={"x-request-id": header_id} if header_id else {},
                             json={"error": {"message": "synthetic failure", "type": "server_error"},
                                   "id": BODY_ID})

    provider = provider_factory(handler)
    if path in ("router", "reasoning_router"):
        result = await _complete(provider, path)
        assert result.text == ""
        assert result.request_id == header_id
    else:
        deltas = []
        with pytest.raises(ProviderError) as raised:
            if path == "stream":
                async for delta in provider.stream(system="test", messages=MESSAGES):
                    deltas.append(delta)
            else:
                await _complete(provider, path)
        assert getattr(raised.value, "request_id", None) == header_id
        if path == "stream":
            assert [delta.request_id for delta in deltas] == ([header_id] if header_id else [])
    assert len(calls) == 1


async def test_midstream_network_failure_keeps_header_already_delivered(provider_factory):
    class InterruptedBody(http.AsyncByteStream):
        async def __aiter__(self):
            payload = {"id": BODY_ID, "created": 1, "model": "qwen3.8-flash", "object": "chat.completion.chunk",
                       "choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}]}
            yield ("data: " + json.dumps(payload) + "\n\n").encode()
            raise http.ReadError("synthetic stream disconnection")

    provider = provider_factory(lambda request: http.Response(
        200, headers={"x-request-id": HEADER_ID, "content-type": "text/event-stream"},
        stream=InterruptedBody()))
    deltas = []
    with pytest.raises(ProviderError) as raised:
        async for delta in provider.stream(system="test", messages=MESSAGES):
            deltas.append(delta)
    assert deltas[0].request_id == HEADER_ID
    assert "".join(delta.text for delta in deltas) == "partial"
    assert raised.value.request_id == HEADER_ID
