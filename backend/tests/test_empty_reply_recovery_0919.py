import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.runtime import Runtime

from app.config import Settings
from app.graph.nodes import MODULE_NODES
from app.providers.base import Completion, StreamDelta, ProviderError
from app.providers.deepseek import DeepSeekProvider
from app.reply_recovery import recover_empty_reply, GENERATION_INTERRUPTED_REPLY
from app.schemas import Message


def recovery_args(provider):
    return dict(provider=provider, system="full safety contract", messages=[Message(role="user", content="a preference")],
        elapsed_seconds=47.5, total_timeout_seconds=60, finish_reason="length",
        usage={"output_tokens": 4000, "reasoning_tokens": 4000})


@pytest.mark.parametrize("mode", ["ok", "empty", "length", "error", "timeout"])
async def test_recovery_once_bounded_and_never_accepts_partial(mode):
    async def call(**kwargs):
        if mode == "error": raise ProviderError("synthetic error")
        if mode == "timeout": await asyncio.sleep(.1)
        return Completion(text="" if mode == "empty" else "安全候选回复", model="same-main",
            finish_reason="length" if mode == "length" else "stop")
    method = AsyncMock(side_effect=call)
    args = recovery_args(SimpleNamespace(complete_without_reasoning=method))
    args.update(elapsed_seconds=.001, total_timeout_seconds=.02)
    result, diagnostic = await recover_empty_reply(**args)
    assert bool(result) == (mode == "ok")
    method.assert_awaited_once_with(system=args["system"], messages=args["messages"])
    assert diagnostic["budget_seconds"] <= .02


async def test_no_retry_on_refusal_normal_stop_or_exhausted_deadline():
    method = AsyncMock()
    for reason, elapsed in [("content_filter", 1), ("stop", 1), ("length", 60)]:
        args = recovery_args(SimpleNamespace(complete_without_reasoning=method))
        args.update(finish_reason=reason, elapsed_seconds=elapsed)
        result, _ = await recover_empty_reply(**args)
        assert result is None
    method.assert_not_awaited()


async def test_cancel_recovery_propagates():
    started = asyncio.Event()
    async def wait(**kwargs):
        started.set()
        await asyncio.sleep(30)
    task = asyncio.create_task(recover_empty_reply(**recovery_args(SimpleNamespace(complete_without_reasoning=wait))))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task


async def test_deepseek_recovery_same_main_model_full_context_no_thinking_or_sdk_retries():
    p = object.__new__(DeepSeekProvider)
    p.model = "same-main"
    p._settings = Settings(_env_file=None)
    create = AsyncMock(return_value=SimpleNamespace(model="same-main", usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content="安全回复"), finish_reason="stop")]))
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    options = []
    p._client.with_options = lambda **kw: (options.append(kw) or p._client)
    args = recovery_args(p)
    result, _ = await recover_empty_reply(**args)
    assert result.text == "安全回复"
    payload = create.call_args.kwargs
    assert payload["model"] == "same-main"
    assert payload["messages"][0]["content"] == args["system"]
    assert payload["messages"][-1]["content"] == args["messages"][-1].content
    assert payload["extra_body"] == {"thinking": {"type": "disabled"}}
    assert payload["max_tokens"] <= 1200 and options == [{"max_retries": 0}]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["ok", "unsafe", "failed", "normal"])
async def test_graph_recovery_keeps_validator_and_reports_real_failure(context, provider, monkeypatch, stream, mode):
    from app.graph import nodes
    events = []
    monkeypatch.setattr(nodes, "_emit", events.append)
    usage = {"input_tokens": 100, "output_tokens": 4000, "reasoning_tokens": 4000}
    provider.complete = AsyncMock(return_value=Completion(text="正常回答" if mode == "normal" else "",
        model="same-main", reasoning_content="abandoned reasoning", usage=usage,
        finish_reason="stop" if mode == "normal" else "length"))
    async def fake_stream(**kw):
        yield StreamDelta(kind="reasoning", text="abandoned reasoning")
        if mode == "normal": yield StreamDelta(kind="content", text="正常回答")
        yield StreamDelta(kind="usage", usage=usage, finish_reason="stop" if mode == "normal" else "length")
    provider.stream = fake_stream
    recovered_text = "" if mode == "failed" else "根据 kb:99999 的说明。" if mode == "unsafe" else "听到了，你喜欢打游戏。"
    provider.complete_without_reasoning = AsyncMock(return_value=Completion(text=recovered_text,
        model="same-main", usage={"output_tokens": 20, "input_tokens": 100}, finish_reason="stop"))
    result = await MODULE_NODES["module_1"]({"user_input": "我喜欢打游戏", "chat_history": []},
        Runtime(context=replace(context, stream=stream)))
    if mode == "normal":
        provider.complete_without_reasoning.assert_not_awaited()
        assert result["final_response"] == "正常回答"
        return
    provider.complete_without_reasoning.assert_awaited_once()
    assert result["usage"]["reasoning_tokens"] == 4000
    assert result["usage"]["output_tokens"] == 4020
    assert result["reasoning_content"] == ""
    if mode == "ok":
        assert result["final_response"] == recovered_text and not result["error"]
        assert result["telemetry"]["answer_validator"]["status"] == "passed"
        assert result["telemetry"]["reply_recovery"]["status"] == "recovered"
    elif mode == "failed":
        assert result["final_response"] == GENERATION_INTERRUPTED_REPLY
        assert result["error"]
        assert result["telemetry"]["error_code"] == "reasoning_budget_exhausted"
        assert "完整性" not in result["final_response"]
    else:
        assert result["final_response"] != recovered_text
        assert result["telemetry"]["answer_validator"]["status"] in {"blocked", "corrected"}
    if stream:
        assert "abandoned reasoning" not in str(events)
        assert mode != "unsafe" or "kb:99999" not in str(events)
