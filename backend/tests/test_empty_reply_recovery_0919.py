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


@pytest.mark.parametrize("reason", ["content_filter", "refusal", "error", "tool_calls", None])
async def test_protocol_flag_cannot_retry_filters_refusals_errors_or_tool_finish(reason):
    method = AsyncMock()
    args = recovery_args(SimpleNamespace(complete_without_reasoning=method))
    args.update(finish_reason=reason, invalid_protocol=True)
    result, diagnostic = await recover_empty_reply(**args)
    assert result is None and not diagnostic['attempted']
    method.assert_not_awaited()


@pytest.mark.parametrize("elapsed", [1, 59.75, 60])
async def test_invalid_protocol_stop_has_one_same_context_attempt_within_original_deadline(elapsed):
    method = AsyncMock(return_value=Completion(text='可用回复', model='same-main', finish_reason='stop'))
    args = recovery_args(SimpleNamespace(complete_without_reasoning=method))
    args.update(finish_reason='stop', invalid_protocol=True, elapsed_seconds=elapsed)
    result, diagnostic = await recover_empty_reply(**args)
    assert diagnostic['reason'] == 'invalid_protocol'
    if elapsed < 60:
        method.assert_awaited_once_with(system=args['system'], messages=args['messages'])
        assert result.text == '可用回复'
        assert diagnostic['budget_seconds'] <= min(8, 60 - elapsed)
    else:
        method.assert_not_awaited()
        assert result is None and diagnostic['status'] == 'budget_exhausted'


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
    assert 'tools' not in payload and 'tool_choice' not in payload


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


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('mode', ['ok', 'unsafe', 'workflow_claim', 'protocol_again', 'thinking_only'])
async def test_invalid_tool_envelope_recovery_never_reuses_payload_or_bypasses_validation(
        context, provider, monkeypatch, stream, mode):
    from app.graph import nodes
    from app.answer_validator import SAFE_REPLY
    events, original_calls = [], []
    monkeypatch.setattr(nodes, '_emit', events.append)
    malformed = '<tool_call>\n{"chat_reply":"丢弃的协议内回复"}</invoke>'
    usage = {'input_tokens': 100, 'output_tokens': 40, 'reasoning_tokens': 10}
    async def original(**kwargs):
        original_calls.append(kwargs)
        return Completion(text=malformed, reasoning_content='丢弃的原始思考', model='same-main',
                          usage=usage, finish_reason='stop')
    provider.complete = original
    async def original_stream(**kwargs):
        original_calls.append(kwargs)
        yield StreamDelta(kind='reasoning', text='丢弃的原始思考')
        for part in ['<tool', '_call>\n', '{"chat_reply":"丢弃的协议内回复"}', '</invoke>']:
            yield StreamDelta(kind='content', text=part)
        yield StreamDelta(kind='usage', usage=usage, finish_reason='stop')
    provider.stream = original_stream
    text = {'ok': '<think>不展示的候选思考</think>\n听到了，先按你的节奏来。',
            'unsafe': '根据 kb:99999 的说明。', 'protocol_again': malformed,
            'workflow_claim': '我们已经确定了这个目标。',
            'thinking_only': '<think>没有正文</think>'}[mode]
    provider.complete_without_reasoning = AsyncMock(return_value=Completion(
        text=text, model='same-main', usage={'output_tokens': 20}, finish_reason='stop'))
    # Even when ordinary validation is configured off, protocol recovery cannot
    # become an escape hatch around the normal integrity/workflow validator.
    settings = context.settings.model_copy(update={'answer_validator_enabled': False})
    module = 'module_2' if mode == 'workflow_claim' else 'module_1'
    result = await MODULE_NODES[module]({'user_input': '我想慢慢来', 'chat_history': []},
        Runtime(context=replace(context, settings=settings, stream=stream)))
    provider.complete_without_reasoning.assert_awaited_once_with(**original_calls[0])
    recovery = result['telemetry']['reply_recovery']
    assert recovery['reason'] == 'invalid_protocol' and recovery['original_finish_reason'] == 'stop'
    assert result['usage'] == {'input_tokens': 100, 'output_tokens': 60, 'reasoning_tokens': 10}
    assert result['reasoning_content'] == ''
    assert result['telemetry']['reply_trace']['raw_model_reply'] == malformed
    assert 'target_module' not in result and 'confirmation_receipt' not in result
    assert '丢弃的协议内回复' not in str(events)
    assert '丢弃的原始思考' not in str(events)
    assert '不展示的候选思考' not in str(events)
    assert '<tool_call>' not in str(events)
    if mode == 'ok':
        assert result['final_response'] == '听到了，先按你的节奏来。'
        assert result['error'] is None and recovery['status'] == 'recovered'
        assert result['telemetry']['answer_validator']['status'] == 'passed'
    elif mode == 'unsafe':
        assert result['error'] and result['final_response'] == SAFE_REPLY
        assert result['telemetry']['answer_validator']['status'] == 'blocked'
        assert not any('kb:99999' in event.get('text', '') for event in events)
    elif mode == 'workflow_claim':
        assert result['final_response'] != text and result['reply_held']
        assert result['telemetry']['answer_validator']['status'] == 'corrected'
        assert not any(text in event.get('text', '') for event in events)
    else:
        assert result['error'] and result['final_response'] == GENERATION_INTERRUPTED_REPLY
        assert result['telemetry']['error_code'] == 'invalid_protocol_completion'
        assert recovery['status'] == ('invalid_completion' if mode == 'protocol_again' else 'invalid_normalized_completion')


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('finish_reason,text', [
    ('stop', ''), ('stop', '我不能帮助这个请求。'),
    ('content_filter', '<tool_call>{"chat_reply":"不能展示"}</invoke>'),
])
async def test_graph_does_not_retry_ordinary_empty_stop_refusal_or_filtered_protocol(
        context, provider, monkeypatch, stream, finish_reason, text):
    from app.graph import nodes
    monkeypatch.setattr(nodes, '_emit', lambda event: None)
    provider.complete = AsyncMock(return_value=Completion(text=text, model='same-main', finish_reason=finish_reason))
    async def original_stream(**kwargs):
        if text:
            yield StreamDelta(kind='content', text=text)
        yield StreamDelta(kind='usage', finish_reason=finish_reason)
    provider.stream = original_stream
    provider.complete_without_reasoning = AsyncMock()
    result = await MODULE_NODES['module_1']({'user_input': '你好', 'chat_history': []},
        Runtime(context=replace(context, stream=stream)))
    provider.complete_without_reasoning.assert_not_awaited()
    if text == '我不能帮助这个请求。':
        assert result['final_response'] == text
