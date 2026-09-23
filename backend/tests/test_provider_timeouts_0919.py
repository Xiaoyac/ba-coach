"""Provider request budgets must bound both complete and streaming calls.

These tests use a fake OpenAI-compatible client so they never touch a real
model endpoint.  ``asyncio.CancelledError`` is intentionally checked
separately: a user closing the chat must cancel the request rather than be
converted into a provider timeout/error.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.providers.base import ProviderError
from app.providers.deepseek import DeepSeekProvider
from app.providers.doubao import DoubaoProvider


def _provider(provider_type):
    provider = object.__new__(provider_type)
    provider.model = "synthetic"
    settings = Settings(_env_file=None)
    # Keep the production validation floor while shortening the test budget
    # after loading settings.  The provider reads this value at call time.
    settings.provider_request_timeout_seconds = 0.01
    settings.router_request_timeout_seconds = 0.01
    settings.background_model_timeout_seconds = 0.01
    settings.doubao_router_model = "synthetic"
    provider._settings = settings
    return provider


def _response(text="ok"):
    return SimpleNamespace(
        model="synthetic",
        usage=None,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, reasoning_content=""),
                finish_reason="stop",
            )
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [DeepSeekProvider, DoubaoProvider])
async def test_complete_timeout_is_bounded_and_reported(provider_type):
    provider = _provider(provider_type)

    async def slow_create(**_kwargs):
        await asyncio.sleep(1)

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=slow_create))
    )

    with pytest.raises(ProviderError, match="request timed out"):
        await provider.complete(system="rules", messages=[])


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [DeepSeekProvider, DoubaoProvider])
async def test_stream_timeout_closes_and_is_reported(provider_type):
    provider = _provider(provider_type)

    class SlowStream:
        closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(1)
            raise StopAsyncIteration

        async def close(self):
            self.closed = True

    stream = SlowStream()
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=stream)))
    )

    with pytest.raises(ProviderError, match="stream timed out"):
        async for _ in provider.stream(system="rules", messages=[]):
            pass
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [DeepSeekProvider, DoubaoProvider])
async def test_user_cancellation_is_not_converted_to_timeout(provider_type):
    provider = _provider(provider_type)
    started = asyncio.Event()

    async def slow_create(**_kwargs):
        started.set()
        await asyncio.sleep(1)

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=slow_create))
    )
    task = asyncio.create_task(provider.complete(system="rules", messages=[]))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [DeepSeekProvider, DoubaoProvider])
async def test_stream_user_cancellation_is_not_converted_to_timeout(provider_type):
    provider = _provider(provider_type)
    started = asyncio.Event()

    class SlowStream:
        closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            started.set()
            await asyncio.sleep(1)
            raise StopAsyncIteration

        async def close(self):
            self.closed = True

    stream = SlowStream()
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=stream)))
    )
    generator = provider.stream(system="rules", messages=[])
    task = asyncio.create_task(generator.__anext__())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await generator.aclose()
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [DeepSeekProvider, DoubaoProvider])
async def test_router_timeout_degrades_to_empty_decision(provider_type):
    provider = _provider(provider_type)

    async def slow_create(**_kwargs):
        await asyncio.sleep(1)

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=slow_create))
    )
    assert await provider.route(system="rules", user="message") == ""
