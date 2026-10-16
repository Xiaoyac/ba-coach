"""Provider-reported cache counters stay distinct from missing telemetry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from app.ai_telemetry import add_ai_event
from app.config import Settings
from app.models import AIExecutionEvent
from app.providers.deepseek import DeepSeekProvider, _cache_usage
from app.schemas import Message


def _provider(usage):
    provider = object.__new__(DeepSeekProvider)
    provider.model = "qwen3.8-flash"
    provider._settings = Settings(_env_file=None, deepseek_model=provider.model,
                                  deepseek_router_model=provider.model)
    response = SimpleNamespace(
        model=provider.model, usage=usage, _request_id="cache-test-request",
        choices=[SimpleNamespace(message=SimpleNamespace(content="reply", reasoning_content="thought"),
                                 finish_reason="stop")],
    )
    create = AsyncMock(return_value=response)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider._client.with_options = Mock(return_value=provider._client)
    return provider, create


class _Stream:
    _request_id = "cache-test-request"

    def __init__(self, usage):
        self.usage = usage
        self.close = AsyncMock()

    async def __aiter__(self):
        yield SimpleNamespace(usage=None, choices=[SimpleNamespace(
            delta=SimpleNamespace(content="reply"), finish_reason="stop")])
        # SDK streaming usage comes in its own final chunk without choices.
        yield SimpleNamespace(usage=self.usage, choices=[])


@pytest.mark.parametrize("path", ["complete", "stream", "recovery", "router", "reasoning_router"])
@pytest.mark.parametrize("details,expected", [
    (SimpleNamespace(cached_tokens=1536, cache_creation_input_tokens=1024),
     {"cache_read_input_tokens": 1536, "cache_creation_input_tokens": 1024}),
    (SimpleNamespace(cached_tokens=0, cache_creation_input_tokens=0),
     {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
    (None, {}),
    (SimpleNamespace(cached_tokens=None), {}),
])
async def test_every_provider_path_preserves_only_reported_cache_counts(path, details, expected):
    usage = SimpleNamespace(prompt_tokens=4096, completion_tokens=32,
                            completion_tokens_details=SimpleNamespace(reasoning_tokens=8),
                            prompt_tokens_details=details)
    provider, create = _provider(usage)
    if path == "stream":
        stream = _Stream(usage)
        create.return_value = stream
        deltas = [delta async for delta in provider.stream(
            system="stable system", messages=[Message(role="user", content="question")])]
        assert [(delta.kind, delta.text) for delta in deltas[:-1]] == [("content", "reply")]
        result = deltas[-1]
        assert result.kind == "usage"
        assert create.call_args.kwargs["stream_options"] == {"include_usage": True}
        stream.close.assert_awaited_once()
    elif path in ("router", "reasoning_router"):
        result = await provider.route_detailed(system="stable system", user="question",
                                               include_reasoning=path == "reasoning_router")
    else:
        method = provider.complete if path == "complete" else provider.complete_without_reasoning
        result = await method(system="stable system", messages=[Message(role="user", content="question")])
    assert result.usage == {
        "input_tokens": 4096, "output_tokens": 32,
        "reasoning_tokens": 0 if path == "recovery" else 8,
        **expected,
    }
    assert result.request_id == "cache-test-request"
    create.assert_awaited_once()
    # Observability must not activate paid explicit-cache behavior.
    assert "cache_control" not in str(create.call_args.kwargs)


@pytest.mark.parametrize("invalid", [None, -1, True, False, 0.5, "256"])
def test_invalid_cache_values_are_not_coerced_into_cache_hits_or_misses(invalid):
    assert _cache_usage(SimpleNamespace(prompt_tokens_details=SimpleNamespace(
        cached_tokens=invalid, cache_creation_input_tokens=invalid))) == {}


def test_missing_usage_and_missing_details_are_unknown():
    assert _cache_usage(None) == {}
    assert _cache_usage(SimpleNamespace(prompt_tokens=10)) == {}
    assert _cache_usage({}) == {}
    assert _cache_usage({"prompt_tokens_details": {"cached_tokens": 0}}) == {"cache_read_input_tokens": 0}


@pytest.mark.parametrize("usage,expected", [
    ({"input_tokens": 4096, "cache_read_input_tokens": 2048, "cache_creation_input_tokens": 0},
     {"cache_read_input_tokens": 2048, "cache_creation_input_tokens": 0}),
    ({"input_tokens": 4096, "cache_read_input_tokens": 0}, {"cache_read_input_tokens": 0}),
    ({"input_tokens": 4096}, {}),
    ({"cache_read_input_tokens": None, "cache_creation_input_tokens": -1}, {}),
])
async def test_event_merges_only_cache_metrics_without_mutating_callers(usage, expected):
    db = SimpleNamespace(add=Mock())
    metadata = {"main_input": {"system": "stable", "messages": []}, "custom": "preserve"}
    event = await add_ai_event(db, stage="main_generation", session_id=None, subject_id=None,
                               provider="deepseek", model_name="qwen3.8-flash", usage=usage,
                               event_metadata=metadata)
    assert event.event_metadata == {**metadata, **expected, **({"cache_usage": expected} if expected else {})}
    assert event.event_metadata is not metadata
    assert set(metadata) == {"main_input", "custom"}
    assert "input_tokens" not in event.event_metadata
    assert event.input_tokens == usage.get("input_tokens")
    db.add.assert_called_once_with(event)


@pytest.mark.parametrize("usage,expected", [(None, None), ({}, None),
                                            ({"cache_read_input_tokens": 0}, {"cache_read_input_tokens": 0})])
async def test_event_without_metadata_does_not_fabricate_missing_cache_values(usage, expected):
    event = await add_ai_event(SimpleNamespace(add=Mock()), stage="main_generation",
                               session_id=None, subject_id=None, provider="deepseek",
                               model_name="qwen3.8-flash", usage=usage)
    assert event.event_metadata == ({**expected, "cache_usage": expected} if expected else expected)


async def test_cache_counts_round_trip_through_existing_event_json(db_sessionmaker):
    async with db_sessionmaker() as db:
        event = await add_ai_event(db, stage="main_generation", session_id=None, subject_id=None,
                                   provider="deepseek", model_name="qwen3.8-flash",
                                   usage={"input_tokens": 4096, "cache_read_input_tokens": 2048},
                                   event_metadata={"context_pipeline": {"engine": "langchain"}})
        await db.commit()
        event_id = event.id
    async with db_sessionmaker() as db:
        saved = (await db.execute(select(AIExecutionEvent).where(AIExecutionEvent.id == event_id))).scalar_one()
        assert saved.input_tokens == 4096
        assert saved.event_metadata == {"context_pipeline": {"engine": "langchain"},
                                        "cache_read_input_tokens": 2048, "cache_usage": {"cache_read_input_tokens": 2048}}
