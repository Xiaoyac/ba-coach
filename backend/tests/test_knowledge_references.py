import dataclasses
import json

import pytest

from app.graph import get_graph
from app.providers.base import Completion, as_text
from app.retrieval import KnowledgeChunk
from app.routes import chat


class KB:
    def __init__(self):
        self.calls = 0
        self.chunks = [KnowledgeChunk("one", "行动可能帮助情绪，不保证立即改善。", "BA", .82),
                       KnowledgeChunk("two", "记录活动和感受。", "记录", .41)]

    async def search(self, **kwargs):
        self.calls += 1
        return self.chunks


@pytest.mark.parametrize("mode", ["approved", "rejected", "timeout", "disabled", "invalid", "empty", "gated"])
async def test_capture_matches_actual_prompt(context, provider, mode):
    kb = KB()
    context = dataclasses.replace(context, knowledge_base=kb)
    context.settings.knowledge_intent_gate_enabled = mode == "gated"
    context.settings.knowledge_mediator_enabled = mode != "disabled"
    provider.route_result = json.dumps({"selected_ids": ["one"] if mode == "approved" else [], "guidance": "根据证据回应。"})
    if mode == "timeout":
        async def timeout(**kwargs):
            raise TimeoutError
        provider.route_detailed = timeout
    if mode == "invalid":
        provider.route_result = "not json"
    if mode == "empty":
        kb.chunks = []
    result = await get_graph().ainvoke({"user_input": "你好" if mode == "gated" else "行动和情绪有什么关系？", "forced_module": "module_2"}, context=context)
    snapshot = result["telemetry"]["knowledge_references"]
    assert snapshot["available"] is True
    if mode in ("empty", "gated", "timeout", "disabled"):
        assert snapshot["mediator_reasoning_content"] is None
    assert len(snapshot["recalled"]) == (0 if mode in ("empty", "gated") else 2)
    assert [x["id"] for x in snapshot["provided"]] == (["one"] if mode == "approved" else [])
    prompt = as_text(provider.systems[-1])
    assert ("行动可能帮助情绪，不保证立即改善。" in prompt) == (mode == "approved")
    assert "记录活动和感受。" not in prompt
    if mode == "timeout":
        assert snapshot["mediator_reason"] == "timeout"
    if mode == "gated":
        assert kb.calls == 0 and snapshot["retrieval_outcome"] == "skipped"


async def test_safety_context_failure_preserves_recalled_but_never_provides(context, provider, monkeypatch):
    from app import knowledge_context, v2_profile
    async def unavailable(*args):
        raise RuntimeError("synthetic unavailable context")
    monkeypatch.setattr(v2_profile, "enabled", lambda: True)
    monkeypatch.setattr(knowledge_context, "assemble_knowledge_context", unavailable)
    from app.graph.nodes import make_module_node, ModuleConfig
    from langgraph.runtime import Runtime
    context = dataclasses.replace(context, knowledge_base=KB(), sessionmaker=object(), prompt_snapshot={})
    context.settings.knowledge_intent_gate_enabled = False
    node = make_module_node("module_1", ModuleConfig())
    result = await node({"user_input":"行动和情绪有什么关系？", "subject_id":"synthetic", "session_id":"synthetic"}, Runtime(context=context))
    snapshot = result["telemetry"]["knowledge_references"]
    assert snapshot["context_withheld"] and len(snapshot["recalled"]) == 2
    assert snapshot["provided"] == []
    assert "行动可能帮助情绪，不保证立即改善。" not in as_text(provider.systems[-1])


@pytest.mark.parametrize("stream", [False, True])
def test_durable_snapshot_owned_lazy_and_deleted(client, auth_headers, register, provider, monkeypatch, stream, caplog):
    kb = KB()
    monkeypatch.setattr(chat, "get_knowledge_base", lambda: kb)
    from app.config import get_settings
    get_settings().knowledge_intent_gate_enabled = False
    provider.route_result = '{"selected_ids":["one"],"guidance":"根据证据回应。"}'
    original = provider.route_detailed
    async def mediator_with_thought(**kwargs):
        if kwargs.get("include_reasoning"):
            return Completion(text=provider.route_result, model="mediator-test",
                reasoning_content="PRIVATE_MEDIATOR_THOUGHT_SENTINEL")
        return await original(**kwargs)
    monkeypatch.setattr(provider, "route_detailed", mediator_with_thought)
    sid = client.post("/api/conversations", headers=auth_headers).json()["session_id"]
    response = client.post("/api/chat/stream" if stream else "/api/chat",
        headers=auth_headers, json={"message":"行动和情绪有什么关系？", "session_id":sid})
    assert response.status_code == 200, response.text
    history = client.get(f"/api/conversations/{sid}", headers=auth_headers).json()
    message = history["messages"][-1]
    assert message["role"] == "assistant" and message["id"]
    path = f"/api/conversations/messages/{message['id']}/knowledge"
    result = client.get(path, headers=auth_headers)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "private, no-store"
    snapshot = result.json()
    assert snapshot["available"] and len(snapshot["recalled"]) == 2
    assert snapshot["mediator_reasoning_content"] == "PRIVATE_MEDIATOR_THOUGHT_SENTINEL"
    assert snapshot["mediator_model"] == "mediator-test"
    assert snapshot["mediator_duration_ms"] >= 0
    assert "PRIVATE_MEDIATOR_THOUGHT_SENTINEL" not in caplog.text
    assert "PRIVATE_MEDIATOR_THOUGHT_SENTINEL" not in as_text(provider.systems[-1])
    assert [x["id"] for x in snapshot["provided"]] == ["one"]
    assert "knowledge_references" not in message  # lazy, not a bulk history payload
    before = (kb.calls, len(provider.seen), len(provider.route_calls))
    kb.chunks = []
    assert client.get(path, headers=auth_headers).json() == snapshot
    assert (kb.calls, len(provider.seen), len(provider.route_calls)) == before
    assert client.get(path).status_code == 401
    assert client.get(path, headers=register(username="other")).status_code == 404
    user_id = next(x["id"] for x in history["messages"] if x["role"] == "user")
    assert client.get(f"/api/conversations/messages/{user_id}/knowledge", headers=auth_headers).status_code == 404
    opening = history["messages"][0]["id"]
    unavailable = client.get(f"/api/conversations/messages/{opening}/knowledge", headers=auth_headers).json()
    assert unavailable["available"] is False and unavailable["recalled"] == []
    # A later turn is not allowed to replace this message's historical snapshot.
    response = client.post("/api/chat", headers=auth_headers,
        json={"message":"继续聊聊行动和情绪", "session_id":sid})
    assert response.status_code == 200
    later = client.get(f"/api/conversations/{sid}", headers=auth_headers).json()["messages"][-1]
    later_snapshot = client.get(f"/api/conversations/messages/{later['id']}/knowledge", headers=auth_headers).json()
    assert later["id"] != message["id"] and later_snapshot["available"] and later_snapshot["recalled"] == []
    assert client.get(path, headers=auth_headers).json() == snapshot
    assert client.delete(f"/api/conversations/{sid}", headers=auth_headers).status_code == 204
    assert client.get(path, headers=auth_headers).status_code == 404
