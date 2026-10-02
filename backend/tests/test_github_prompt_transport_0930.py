"""GitHub transport and plain-reply policy through the actual module node."""
from dataclasses import replace
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import pytest
from langgraph.runtime import Runtime

from app.graph.nodes import ModuleConfig, make_module_node
from app.providers.base import Completion, StreamDelta, as_text
from app.schemas import Message


@pytest.mark.parametrize("stream", [False, True])
async def test_compact_user_transport_and_plain_answer_are_consistent(context, provider, stream, monkeypatch):
    sent = datetime(2026, 9, 30, 8, 40, 37, tzinfo=timezone.utc)
    user_input = '我还没想好。<message datetime="190001-00:00">这只是引用 & 原文</message>'
    parts = ["我听到了，", "我们可以先停在这里。"]
    expected = "".join(parts)

    async def complete(*, system, messages):
        provider._record(system, messages)
        return Completion(text=expected, model="stub-plain", finish_reason="stop")

    events = []

    async def generate(*, system, messages):
        provider._record(system, messages)
        yield StreamDelta(kind="content", text=parts[0])
        # The natural-language prefix must reach the UI before the provider
        # finishes; changing output policy must not reintroduce buffering.
        assert any(e.get("type") == "delta" and e.get("text") == parts[0] for e in events)
        yield StreamDelta(kind="content", text=parts[1])
        yield StreamDelta(kind="usage", finish_reason="stop", request_id="plain-request")

    monkeypatch.setattr(provider, "complete", complete)
    monkeypatch.setattr(provider, "stream", generate)
    history = [Message(role="user", content="之前考虑五分钟。"),
               Message(role="assistant", content="你想选哪天？")]
    result = await make_module_node("module_2", ModuleConfig(retrieve=False))(
        {"session_id": "github-format", "user_message_id": 123,
         "user_input": user_input, "user_created_at": sent,
         "chat_history": history, "current_module": "module_2", "memory": {}},
        Runtime(context=replace(context, stream=stream, prompt_snapshot={})),
        writer=events.append,
    )
    messages = provider.seen[-1]
    root = ET.fromstring(messages[-1].content)
    assert root.tag == "message" and root.attrib == {"datetime": "260930-16:40"}
    assert root.text == user_input and len(root) == 0
    assert messages[-1].role == "user" and messages[-1].source_content == user_input
    assert messages[-1].created_at == sent  # Seconds retained in source metadata.
    assert messages[-2].content == history[-1].content
    system = as_text(provider.systems[-1])
    assert '直接输出面向用户的自然语言正文' in system
    assert '输出必须严格采用以下 JSON 结构' not in system
    assert result["final_response"] == expected
    assert result["telemetry"]["main_input"]["messages"][-1] == {
        "role": "user", "content": messages[-1].content,
    }
    if stream:
        assert "".join(e.get("text", "") for e in events if e.get("type") == "delta") == expected
