import dataclasses

import pytest

from app.graph import get_graph
from app.providers.base import Completion, StreamDelta


@pytest.mark.parametrize("stream", [False, True])
async def test_ordinary_quality_warning_keeps_actual_reply(context, provider, monkeypatch, stream):
    original = "从明天开始每天跑步。"

    async def complete(**kwargs):
        return Completion(text=original, model="test")

    async def chunks(**kwargs):
        yield StreamDelta(kind="content", text=original)

    monkeypatch.setattr(provider, "complete", complete)
    monkeypatch.setattr(provider, "stream", chunks)
    context = dataclasses.replace(context, stream=stream)
    events, final = [], None
    async for kind, value in get_graph().astream(
        {"user_input": "继续", "forced_module": "module_1"}, context=context,
        stream_mode=["custom", "values"],
    ):
        if kind == "custom":
            events.append(value)
        else:
            final = value
    assert final["final_response"] == original
    assert not final.get("reply_held")
    diagnostic = final["telemetry"]["answer_validator"]
    assert diagnostic["status"] == "review"
    assert diagnostic["display_source"] == "original_model_reply"
    assert "replacement_reply" not in diagnostic
    if stream:
        assert "".join(e.get("text", "") for e in events if e.get("type") == "delta") == original
