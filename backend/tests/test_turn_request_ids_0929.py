"""Actual chat generation persists request IDs into the per-reply debug API."""
from test_admin_sandbox import sandbox_admin_headers
import json

import pytest

from app.providers.base import Completion, ProviderError, StreamDelta


@pytest.mark.parametrize("interrupted", [False, True])
def test_stream_header_request_id_survives_reply_persistence(
    client, sandbox_admin_headers, provider, monkeypatch, interrupted,
):
    async def stream(**kwargs):
        yield StreamDelta(kind="usage", request_id="bailian-main-header")
        yield StreamDelta(kind="content", text="这是本轮的回复。")
        if interrupted:
            raise ProviderError("stream disconnected")
        yield StreamDelta(kind="usage", finish_reason="stop")

    async def router(**kwargs):
        return Completion(text=json.dumps({"target_module": "1"}), model="router-test",
                          request_id="bailian-router-header")

    monkeypatch.setattr(provider, "stream", stream)
    monkeypatch.setattr(provider, "route_with_reasoning", router)
    created = client.post("/api/conversations", headers=sandbox_admin_headers).json()
    sid = created["session_id"]
    response = client.post("/api/chat/stream", headers=sandbox_admin_headers,
                           json={"session_id": sid, "message": "你好"})
    assert response.status_code == 200
    conversation = client.get(f"/api/conversations/{sid}", headers=sandbox_admin_headers).json()
    reply = conversation["messages"][-1]
    assert reply["role"] == "assistant" and reply["content"] == "这是本轮的回复。"
    requests = client.get(f"/api/conversations/messages/{reply['id']}/requests",
                          headers=sandbox_admin_headers).json()["requests"]
    # All model stages now inherit the current user binding. A risk check that
    # returned no provider ID remains unknown, never borrowed from another call.
    assert {item["stage"]: item["request_id"] for item in requests if item["request_id"]} == {
        "main_generation": "bailian-main-header", "module_router": "bailian-router-header",
    }


async def test_mediator_preserves_request_id_in_metrics(provider, monkeypatch):
    from app.config import Settings
    from app.knowledge_mediator import mediate_knowledge
    from app.retrieval import KnowledgeChunk

    async def complete(**kwargs):
        return Completion(text='{"decision":"not_needed","selections":[],"note":"没有新知识需求"}',
                          model="qwen-test", request_id="bailian-mediator-header")

    monkeypatch.setattr(provider, "route_detailed", complete)
    _, _, metrics = await mediate_knowledge(state={"user_input": "行动有什么帮助"},
        module="module_1", knowledge=[KnowledgeChunk(id="k1", text="行动可能帮助情绪", source="test")],
        provider=provider, settings=Settings(_env_file=None, knowledge_mediator_enabled=True))
    assert metrics["request_id"] == "bailian-mediator-header"


async def test_recovery_http_error_retains_request_id():
    from types import SimpleNamespace
    from app.reply_recovery import recover_empty_reply

    async def fail(**kwargs):
        error = ProviderError("upstream unavailable")
        error.request_id = "bailian-recovery-error"
        raise error

    result, diagnostic = await recover_empty_reply(provider=SimpleNamespace(complete_without_reasoning=fail),
        system="test", messages=[], elapsed_seconds=1, total_timeout_seconds=60,
        finish_reason="length", usage={})
    assert result is None
    assert diagnostic["status"] == "provider_error"
    assert diagnostic["request_id"] == "bailian-recovery-error"
