"""MemOS Cloud wire contract — URL, auth, payload, and response parsing."""

from __future__ import annotations

import json

import httpx
import pytest

from app.memos_integration import MemosIntegrationManager


@pytest.mark.asyncio
async def test_search_uses_openmem_endpoint_and_parses_memories(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "code": 0,
                "data": {
                    "memory_detail_list": [{"memory_value": "曾设定散步目标"}],
                    "preference_detail_list": [{"preference": "偏好晚间活动"}],
                },
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    manager = MemosIntegrationManager(
        base_url="https://memos.memtensor.cn/api/openmem/v1/",
        api_key="test-key",
    )

    result = await manager.retrieve_recent_memos("subject-1", query="最近不想出门")

    assert result == ["曾设定散步目标", "偏好晚间活动"]
    assert str(requests[0].url) == (
        "https://memos.memtensor.cn/api/openmem/v1/search/memory"
    )
    assert requests[0].headers["authorization"] == "Token test-key"
    assert json.loads(requests[0].content) == {
        "query": "最近不想出门",
        "user_id": "subject-1",
        "memory_limit_number": 5,
    }


@pytest.mark.asyncio
async def test_save_uses_add_message_contract(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json={"code": 0, "message": "ok"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    manager = MemosIntegrationManager(
        base_url="https://memos.memtensor.cn/api/openmem/v1",
        api_key="test-key",
    )

    assert await manager.save_memo("subject-1", "完成模块一")
    assert requests[0].url.path.endswith("/api/openmem/v1/add/message")
    assert json.loads(requests[0].content) == {
        "user_id": "subject-1",
        "conversation_id": "ba-coach-subject-1",
        "messages": [{"role": "assistant", "content": "完成模块一"}],
    }

