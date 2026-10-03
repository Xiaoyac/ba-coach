"""Final-response recovery must retain committed tools and native roles."""
import json
from types import SimpleNamespace as NS

import pytest

from app.config import Settings
from app.pa_tool_loop import PAToolReply
from app.providers.base import as_text
from app.providers.tool_calling import stream_openai_tools
from app.schemas import Message


class NativeStream:
    def __init__(self, chunks, request_id):
        self.chunks = chunks
        self.response = NS(headers={"x-request-id": request_id})
        self.closed = False

    def __aiter__(self):
        async def iterate():
            for chunk in self.chunks:
                yield chunk
        return iterate()

    async def close(self):
        self.closed = True


def chunk(*, content=None, reasoning=None, tool=None, finish=None):
    calls = None if tool is None else [NS(index=0, id="tool-" + tool,
        function=NS(name=tool, arguments="{}"))]
    return NS(usage=NS(prompt_tokens=12, completion_tokens=3,
            completion_tokens_details=NS(reasoning_tokens=2), prompt_tokens_details=None),
        choices=[NS(finish_reason=finish, delta=NS(content=content,
            reasoning_content=reasoning, tool_calls=calls))])


@pytest.mark.parametrize("empty_finish,first_content,retry", [
    ("stop", None, True), ("length", None, True),
    ("content_filter", None, False), ("stop", "目标已确认。", False),
    ("length", "目标已确认，", False),
])
async def test_native_empty_final_retries_without_reexecuting_confirmation(empty_finish, first_content, retry):
    """Exercise real native transport, not a fake high-level completion API.

    The SDK is deterministic and never opens a connection. Its final response
    first has no visible output, then succeeds. Recovery must be no-tools
    and retain the real committed result plus the post-confirmation prompt.
    """
    class Client:
        def __init__(self):
            self.requests, self.streams = [], []
            self.chat = NS(completions=NS(create=self.create))

        async def create(self, **kwargs):
            self.requests.append(kwargs)
            number = len(self.requests)
            if number == 1:
                chunks = [chunk(tool="get_pa_card", finish="tool_calls")]
            elif number == 2:
                chunks = [chunk(tool="confirm_pa_card", finish="tool_calls")]
            elif number == 3:
                chunks = [chunk(content=first_content, reasoning="Only a provider reasoning channel.", finish=empty_finish)]
            else:
                assert number == 4, "Recovery must not run an unbounded retry loop"
                chunks = [chunk(content="目标已确认，接下来可以聊聊怎样记录。", finish="stop")]
            stream = NativeStream(chunks, f"native-final-{number}")
            self.streams.append(stream)
            return stream

    class Provider:
        name, model, thinking_override = "deepseek", "synthetic-tools", False

        def __init__(self):
            self._settings = Settings(_env_file=None)
            self._client = Client()

        async def stream_tools(self, **kwargs):
            async for delta in stream_openai_tools(self, **kwargs):
                yield delta

    class Executor:
        definitions = [{"type": "function", "function": {"name": name,
            "parameters": {"type": "object", "properties": {}}}} for name in
            ("get_pa_card", "confirm_pa_card", "continue_pa_conversation")]

        def __init__(self):
            self.trace, self.displays, self.operations = [], [], []

        async def execute(self, call):
            name = call["function"]["name"]
            self.operations.append(name)
            if name == "get_pa_card":
                return {"status": "ok", "state_version": 8}
            assert name == "confirm_pa_card"
            return {"status": "confirmed", "next_module": "module_3", "state_version": 9}

    async def transition(target):
        assert target == "module_3"
        return "M3_AFTER_COMMIT: 使用已成功确认的计划继续对话。"

    provider, executor, telemetry = Provider(), Executor(), {}
    output = [delta async for delta in PAToolReply(provider, executor, max_rounds=5,
        telemetry=telemetry, transition_prompt=transition).stream(system="M2_INITIAL",
            messages=[Message(role="user", content="确认，就按这个计划试试。")])]
    visible = "".join(delta.text for delta in output if delta.kind == "content")
    assert visible == ("目标已确认，接下来可以聊聊怎样记录。" if retry else first_content or "")
    assert executor.operations == ["get_pa_card", "confirm_pa_card"]
    requests = provider._client.requests
    assert len(requests) == (4 if retry else 3)
    assert all(request["tool_choice"] == "none" for request in requests[2:])
    for request in requests[2:]:
        assert "M3_AFTER_COMMIT" in as_text(request["messages"][0]["content"])
        user_messages = [message for message in request["messages"] if message["role"] == "user"]
        assert len(user_messages) == 1 and "确认，就按这个计划试试。" in user_messages[0]["content"]
        results = [message for message in request["messages"] if message["role"] == "tool"]
        assert json.loads(results[-1]["content"])["state_version"] == 9
        assert results[-1]["tool_call_id"] == "tool-confirm_pa_card"
    assert all(stream.closed for stream in provider._client.streams)
    assert [request["request_id"] for request in telemetry["pa_tools"]["requests"]] == [
        f"native-final-{number}" for number in range(1, len(requests) + 1)]


async def test_native_refusal_is_not_mistaken_for_retryable_empty_stop():
    from unittest.mock import AsyncMock
    refused = chunk(finish="stop")
    refused.choices[0].delta.refusal = "I cannot provide that response."
    stream = NativeStream([refused], "explicit-provider-refusal")
    provider = NS(name="deepseek", model="synthetic-tools", thinking_override=False,
        _settings=Settings(_env_file=None), _client=NS(chat=NS(completions=NS(create=AsyncMock(return_value=stream)))))
    output = [delta async for delta in stream_openai_tools(provider, system="policy",
        messages=[{"role": "user", "content": "synthetic request"}], tools=[], tool_choice="none")]
    visible = "".join(delta.text for delta in output if delta.kind == "content")
    last_usage = next(delta for delta in reversed(output) if delta.kind == "usage")
    assert visible or last_usage.finish_reason not in {None, "stop", "length"}, (
        "Explicit delta.refusal must not be discarded into an ordinary empty stop eligible for recovery")
    assert stream.closed


async def test_two_empty_final_responses_stop_after_one_retry():
    from app.providers.base import StreamDelta
    class Provider:
        requests = 0
        async def stream_tools(self, **kwargs):
            self.requests += 1
            assert kwargs["tool_choice"] == "none"
            assert self.requests <= 2, "Only one recovery is allowed"
            yield StreamDelta(kind="usage", usage={"output_tokens": 7},
                request_id=f"empty-{self.requests}", finish_reason="stop")
    class Executor:
        definitions, trace, displays = [], [], []
        async def execute(self, _):
            pytest.fail("Final wording recovery cannot execute an operation")
    provider, telemetry = Provider(), {}
    output = [delta async for delta in PAToolReply(provider, Executor(), max_rounds=0,
        telemetry=telemetry).stream(system="policy", messages=[Message(role="user", content="确认")])]
    assert provider.requests == 2
    assert not any(delta.text.strip() for delta in output if delta.kind == "content")
    assert output[-1].usage["output_tokens"] == 14
    assert telemetry["pa_tools"]["final_reply_recovery"]["status"] == "empty_again"


async def test_cancellation_during_final_recovery_closes_generator_and_does_not_retry():
    import asyncio
    from app.providers.base import StreamDelta
    started, closed = asyncio.Event(), asyncio.Event()
    class Provider:
        requests = 0
        async def stream_tools(self, **kwargs):
            self.requests += 1
            assert kwargs["tool_choice"] == "none"
            if self.requests == 1:
                yield StreamDelta(kind="usage", finish_reason="stop")
                return
            assert self.requests == 2
            try:
                started.set()
                await asyncio.Event().wait()
                yield StreamDelta(kind="content", text="must never appear")
            finally:
                closed.set()
    class Executor:
        definitions, trace, displays = [], [], []
        async def execute(self, _):
            pytest.fail("Cancelled final wording cannot execute an operation")
    provider = Provider()
    async def consume():
        return [delta async for delta in PAToolReply(provider, Executor(), max_rounds=0,
            telemetry={}).stream(system="policy", messages=[Message(role="user", content="确认")])]
    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and provider.requests == 2
