"""Assert serialized Ark requests, not only internal thinking flags."""
import json
from types import SimpleNamespace

import openai
import pytest
from openai import _base_client

from app.config import Settings
from app.generation_policy import (
    deep_reply_thinking_options, is_ark_kimi, main_thinking_options,
    native_thinking_options, reply_effort_options,
)
from app.providers.deepseek import DeepSeekProvider
from app.schemas import Message

http = getattr(_base_client, "httpx2", None) or _base_client.httpx
URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
MESSAGES = [Message(role="user", content="好的")]


def settings(**overrides):
    return Settings(_env_file=None, deepseek_api_key="offline-test-key",
                    deepseek_base_url=URL, deepseek_model="kimi-k3",
                    deepseek_router_model="kimi-k3", **overrides)


@pytest.mark.parametrize("effort", ["low", "high", "max"])
def test_three_efforts_and_no_unsupported_switch(effort):
    cfg = settings()
    assert reply_effort_options(cfg, "deepseek", model="kimi-k3") == ("low", "high", "max")
    assert deep_reply_thinking_options(cfg, "deepseek", model="kimi-k3", effort=effort) == {
        "reasoning_effort": effort}


@pytest.mark.parametrize("enabled", [False, True])
def test_native_off_is_low_and_on_defaults_to_low(enabled):
    assert native_thinking_options(settings(), "deepseek", enabled=enabled) == {
        "reasoning_effort": "low"}


def test_explicit_main_effort_beats_fast_ack_but_off_means_low():
    cfg = settings(deepseek_reasoning_effort="high")
    assert main_thinking_options(cfg, "deepseek", MESSAGES) == {"reasoning_effort": "low"}
    assert main_thinking_options(cfg, "deepseek", MESSAGES, enabled_override=True) == {
        "reasoning_effort": "high"}
    assert main_thinking_options(cfg, "deepseek", MESSAGES, enabled_override=False) == {
        "reasoning_effort": "low"}


@pytest.mark.parametrize("url,model", [
    ("https://ark.cn-beijing.volces.com/api/v3", "kimi-k3"),
    ("https://ark.cn-beijing.volces.com.invalid/api/coding/v3", "kimi-k3"),
    (URL, "doubao-seed-2-1-turbo-260628"),
    ("https://api.moonshot.cn/v1", "kimi-k3"),
])
def test_capability_does_not_leak_to_other_models_or_channels(url, model):
    cfg = SimpleNamespace(deepseek_base_url=url, deepseek_model=model)
    assert not is_ark_kimi(cfg, "deepseek")
    assert native_thinking_options(cfg, "deepseek", enabled=False) == {
        "thinking": {"type": "disabled"}}


def test_invalid_effort_is_not_silently_sent():
    with pytest.raises(ValueError):
        deep_reply_thinking_options(settings(), "deepseek", model="kimi-k3", effort="medium")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["complete", "stream", "router", "reasoning_router", "recovery", "lead"])
async def test_real_sdk_wire_retains_roles_effort_budget_and_request_id(path):
    payloads = []

    def handler(request):
        assert str(request.url) == URL + "/chat/completions"
        payload = json.loads(request.content)
        payloads.append(payload)
        message = {"role": "assistant", "content": "OK", "reasoning_content": "brief reasoning"}
        result = {"id": "completion-id", "object": "chat.completion", "model": "kimi-k3", "created": 1,
                  "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}
        headers = {"x-request-id": "ark-request-id"}
        if payload.get("stream"):
            result.update(object="chat.completion.chunk", choices=[
                {"index": 0, "delta": message, "finish_reason": "stop"}])
            return http.Response(200, headers={**headers, "content-type": "text/event-stream"},
                                 content=("data: " + json.dumps(result) + "\n\ndata: [DONE]\n\n").encode())
        return http.Response(200, headers=headers, json=result)

    p = object.__new__(DeepSeekProvider)
    p.model = "kimi-k3"
    p._settings = settings(deepseek_max_tokens=160 if path == "lead" else 4000)
    p._client = openai.AsyncOpenAI(api_key="offline-test-key", base_url=URL, max_retries=0,
        http_client=http.AsyncClient(transport=http.MockTransport(handler)))
    p = p.with_thinking(False if path != "reasoning_router" else None)
    try:
        if path == "stream":
            result = [d async for d in p.stream(system="unchanged system", messages=MESSAGES)]
            assert "".join(d.text for d in result if d.kind == "content") == "OK"
            assert result[-1].request_id == "ark-request-id"
        elif path in {"router", "reasoning_router"}:
            result = await p.route_detailed(system="unchanged system", user="好的",
                include_reasoning=path == "reasoning_router",
                reasoning_effort="max" if path == "reasoning_router" else None, max_tokens=64)
            assert result.text == "OK"
            assert result.reasoning_content == "brief reasoning"
            assert result.request_id == "ark-request-id"
        else:
            method = p.complete_without_reasoning if path == "recovery" else p.complete
            result = await method(system="unchanged system", messages=MESSAGES)
            assert result.text == "OK" and result.request_id == "ark-request-id"
        assert len(payloads) == 1
        body = payloads[0]
        assert body["reasoning_effort"] == ("max" if path == "reasoning_router" else "low")
        assert not {"thinking", "enable_thinking", "thinking_budget"} & body.keys()
        assert body["max_tokens"] >= 2048
        assert body["messages"][-1] == {"role": "user", "content": "好的"}
        assert body["messages"][0]["role"] == "system"
    finally:
        await p._client.close()
