"""HTTP-surface tests for /api/chat.

Graph internals are covered in test_graph.py; these assert the API contract —
status codes, response shape, SSE framing — over the compiled graph.
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AIExecutionEvent
from app.providers.base import Completion, ProviderError, StreamDelta


def sse_events(payload: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, data) pairs."""
    events: list[tuple[str, dict]] = []
    for frame in payload.split("\n\n"):
        name, data = None, None
        for line in frame.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if name and data is not None:
            events.append((name, data))
    return events


# ---------------------------------------------------------------------------
# Meta endpoints
# ---------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_modules(client: TestClient) -> None:
    assert client.get("/api/modules").json()["modules"] == [
        "module_1",
        "module_2",
        "module_3",
        "module_4",
    ]


def test_describe_graph(client: TestClient) -> None:
    body = client.get("/api/graph").json()
    assert "analyze_intent" in body["nodes"]
    assert "update_memory_and_format" in body["nodes"]
    conditional = [e for e in body["edges"] if e["conditional"]]
    # The crisis branch shares the gate's fan-out with the four modules.
    assert {e["target"] for e in conditional} == {
        "crisis",
        "pre_reply_router",
        "module_1",
        "module_2",
        "module_3",
        "module_4",
    }


# ---------------------------------------------------------------------------
# JSON endpoint
# ---------------------------------------------------------------------------


def test_chat_mints_session_id(client: TestClient) -> None:
    body = client.post("/api/chat", json={"message": "hello"}).json()
    assert body["session_id"]
    assert body["reply"] == "saw 1 messages"
    assert body["reply_module"] in {"module_1", "module_2", "module_3", "module_4"}
    assert body["next_module"] == body["reply_module"]
    assert body["routing_pending"] is False
    assert body["model"] == "stub-1"


def test_json_chat_returns_reasoning_separately(client: TestClient, provider) -> None:
    provider.reasoning_result = "先分析，再回答。"
    body = client.post("/api/chat", json={"message": "hello"}).json()
    assert body["reply"] == "saw 1 messages"
    assert body["reasoning_content"] == "先分析，再回答。"


def test_json_chat_returns_router_reasoning_separately(
    client: TestClient, provider
) -> None:
    provider.route_result = '{"target_module":"1"}'
    provider.route_reasoning_result = "先核对模块一的退出条件，再决定维持当前模块。"
    body = client.post("/api/chat", json={"message": "hello"}).json()
    assert provider.route_reasoning_result in body["routing_reasoning_content"]
    assert body["router_model_name"] == "stub-router-1"


def test_json_chat_reply_envelope_is_not_exposed(
    client: TestClient, provider, monkeypatch
) -> None:
    async def wrapped(**_kwargs) -> Completion:
        return Completion(
            text='{"chat_reply":"这是用户真正应该看到的回复"}',
            model="stub-1",
            usage={},
        )

    monkeypatch.setattr(provider, "complete", wrapped)
    body = client.post("/api/chat", json={"message": "测试"}).json()
    assert body["reply"] == "这是用户真正应该看到的回复"
    assert "chat_reply" not in body["reply"]


def test_history_accumulates_across_turns(client: TestClient, provider) -> None:
    first = client.post("/api/chat", json={"message": "one"}).json()
    session_id = first["session_id"]

    second = client.post(
        "/api/chat", json={"message": "two", "session_id": session_id}
    ).json()

    assert second["session_id"] == session_id
    assert second["reply"] == "saw 3 messages"
    assert [m.content for m in provider.seen[-1]] == ["one", "saw 1 messages", "<user_message>two</user_message>"]


def test_unknown_session_id_starts_fresh(client: TestClient) -> None:
    body = client.post(
        "/api/chat", json={"message": "hi", "session_id": "not-a-real-session"}
    ).json()
    assert body["session_id"] != "not-a-real-session"
    assert body["reply"] == "saw 1 messages"


def test_explicit_module_is_reported(client: TestClient) -> None:
    body = client.post("/api/chat", json={"message": "hi", "module": "module_4"}).json()
    assert body["reply_module"] == "module_4"
    assert body["routed_by"] == "explicit"


def test_unknown_module_is_rejected(client: TestClient) -> None:
    response = client.post("/api/chat", json={"message": "hi", "module": "module_9"})
    assert response.status_code == 400
    assert "module_9" in response.json()["detail"]


def test_message_cannot_be_empty(client: TestClient) -> None:
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_provider_failure_returns_502(client: TestClient, provider) -> None:
    provider.fail_with = ProviderError("upstream exploded")
    response = client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 502
    assert "upstream exploded" in response.json()["detail"]


def test_authenticated_provider_failure_is_recorded_in_ai_telemetry(
    client: TestClient, register, provider, db_sessionmaker
) -> None:
    headers = register("telemetryfailure")
    provider.fail_with = ProviderError("upstream exploded")

    response = client.post("/api/chat", json={"message": "hi"}, headers=headers)
    assert response.status_code == 502

    async def read_event() -> AIExecutionEvent:
        async with db_sessionmaker() as db:
            result = await db.execute(
                select(AIExecutionEvent).where(
                    AIExecutionEvent.stage == "main_generation"
                )
            )
            return result.scalar_one()

    event = asyncio.run(read_event())
    assert event.error_code == "provider_error"
    assert event.provider == provider.name
    assert event.duration_ms is not None


def test_saved_model_preference_drives_chat(
    client: TestClient, register, provider, monkeypatch
) -> None:
    from app.routes import chat as chat_route

    headers = register(username="modelchoice")
    assert client.patch(
        "/api/profile", json={"preferred_provider": "doubao"}, headers=headers
    ).status_code == 200

    selected: list[str | None] = []

    def capture(name=None):
        selected.append(name)
        return provider

    monkeypatch.setattr(chat_route, "get_provider", capture)
    assert client.post("/api/chat", json={"message": "你好"}, headers=headers).status_code == 200
    assert selected == ["doubao", "deepseek"]


def test_explicit_provider_overrides_saved_preference(
    client: TestClient, register, provider, monkeypatch
) -> None:
    from app.routes import chat as chat_route

    headers = register(username="modeloverride")
    client.patch(
        "/api/profile", json={"preferred_provider": "doubao"}, headers=headers
    )
    selected: list[str | None] = []

    def capture(name=None):
        selected.append(name)
        return provider

    monkeypatch.setattr(chat_route, "get_provider", capture)
    response = client.post(
        "/api/chat",
        json={"message": "你好", "provider": "deepseek"},
        headers=headers,
    )
    assert response.status_code == 200
    assert selected == ["deepseek", "deepseek"]


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_session_reports_module_and_memory(client: TestClient) -> None:
    session_id = client.post(
        "/api/chat", json={"message": "hi", "module": "module_2"}
    ).json()["session_id"]

    info = client.get(f"/api/sessions/{session_id}").json()
    assert info["next_module"] == "module_2"
    assert info["message_count"] == 2
    assert info["memory"]["turn_count"] == "1"
    assert info["memory"]["last_module"] == "module_2"


def test_delete_session_clears_history(client: TestClient) -> None:
    session_id = client.post("/api/chat", json={"message": "one"}).json()["session_id"]
    assert client.delete(f"/api/sessions/{session_id}").status_code == 204
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


# ---------------------------------------------------------------------------
# SSE endpoint
# ---------------------------------------------------------------------------


def test_stream_emits_meta_deltas_and_done(client: TestClient) -> None:
    with client.stream("POST", "/api/chat/stream", json={"message": "hi"}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = sse_events("".join(response.iter_text()))

    kinds = [name for name, _ in events]
    assert kinds[0] == "meta"  # session id before anything else
    assert events[0][1]["model"] == "stub-1"
    assert kinds.count("delta") == 3
    assert "done" in kinds
    assert events[-1] == ("persisted", {"saved": False})  # anonymous, no durable account
    assert "".join(d["text"] for n, d in events if n == "delta") == "hello"


def test_stream_holds_and_unwraps_chat_reply_json(
    client: TestClient, provider, monkeypatch
) -> None:
    async def wrapped_stream(**_kwargs):
        for part in ('{"chat_', 'reply":"管理', '员，已经修复。"}'):
            yield StreamDelta(kind="content", text=part)

    monkeypatch.setattr(provider, "stream", wrapped_stream)
    with client.stream("POST", "/api/chat/stream", json={"message": "测试"}) as response:
        events = sse_events("".join(response.iter_text()))
    visible = "".join(data["text"] for name, data in events if name == "delta")
    assert visible == "管理员，已经修复。"
    assert "chat_reply" not in visible


def test_stream_meta_carries_routing_decision(client: TestClient) -> None:
    with client.stream(
        "POST", "/api/chat/stream", json={"message": "hi", "module": "module_4"}
    ) as response:
        events = sse_events("".join(response.iter_text()))

    routing = [d for n, d in events if n == "meta" and "reply_module" in d]
    assert routing and routing[0]["reply_module"] == "module_4"
    assert routing[0]["routed_by"] == "explicit"


def test_stream_does_not_leak_trace_events(client: TestClient) -> None:
    with client.stream("POST", "/api/chat/stream", json={"message": "hi"}) as response:
        events = sse_events("".join(response.iter_text()))
    assert not any(name == "trace" for name, _ in events)


def test_stream_reply_is_persisted(client: TestClient, provider) -> None:
    with client.stream("POST", "/api/chat/stream", json={"message": "hi"}) as response:
        events = sse_events("".join(response.iter_text()))
    session_id = events[0][1]["session_id"]

    assert client.get(f"/api/sessions/{session_id}").json()["message_count"] == 2

    follow_up = client.post(
        "/api/chat", json={"message": "again", "session_id": session_id}
    ).json()
    assert [m.content for m in provider.seen[-1]] == ["hi", "hello", "<user_message>again</user_message>"]
    assert follow_up["session_id"] == session_id


def test_stream_reasoning_is_separate_and_persisted(
    client: TestClient, auth_headers, provider
) -> None:
    provider.reasoning_result = "这是模型的深度思考。"
    with client.stream(
        "POST", "/api/chat/stream", json={"message": "hi"}, headers=auth_headers
    ) as response:
        events = sse_events("".join(response.iter_text()))

    reasoning = "".join(
        data["text"] for name, data in events if name == "reasoning_delta"
    )
    visible = "".join(data["text"] for name, data in events if name == "delta")
    assert reasoning == "这是模型的深度思考。"
    assert visible == "hello"

    session_id = events[0][1]["session_id"]
    detail = client.get(
        f"/api/conversations/{session_id}", headers=auth_headers
    ).json()
    assistant = detail["messages"][-1]
    assert assistant["content"] == "hello"
    assert assistant["reasoning_content"] == reasoning
    assert assistant["model_name"] == "stub-1"


def test_stream_repairs_provider_thinking_tags_before_any_user_visible_delta(
    client: TestClient, auth_headers, provider, monkeypatch
) -> None:
    async def malformed_stream(**_kwargs):
        yield StreamDelta(kind="content", text="<thi")
        yield StreamDelta(kind="reasoning", text="response重复回答")
        yield StreamDelta(
            kind="content",
            text="nking>内部分析</thinking>\n\n给用户的回答",
        )
        yield StreamDelta(kind="usage", usage={"output_tokens": 12})

    monkeypatch.setattr(provider, "stream", malformed_stream)
    with client.stream(
        "POST", "/api/chat/stream", json={"message": "hi"}, headers=auth_headers
    ) as response:
        events = sse_events("".join(response.iter_text()))

    visible = "".join(data["text"] for name, data in events if name == "delta")
    reasoning = "".join(
        data["text"] for name, data in events if name == "reasoning_delta"
    )
    assert visible == "给用户的回答"
    assert reasoning == "内部分析"
    assert "<thinking>" not in visible
    assert "response" not in reasoning

    session_id = events[0][1]["session_id"]
    detail = client.get(
        f"/api/conversations/{session_id}", headers=auth_headers
    ).json()
    assert detail["messages"][-1]["content"] == visible
    assert detail["messages"][-1]["reasoning_content"] == reasoning


def test_stream_routes_before_visible_reply_and_persists_decision(
    client: TestClient, auth_headers, provider
) -> None:
    provider.route_result = '{"target_module":"1"}'
    provider.route_reasoning_result = "退出条件尚未满足，因此不跳转。"
    with client.stream(
        "POST", "/api/chat/stream", json={"message": "hi"}, headers=auth_headers
    ) as response:
        events = sse_events("".join(response.iter_text()))

    kinds = [name for name, _ in events]
    assert kinds.index("routing_reasoning") < kinds.index("delta")
    assert "done" in kinds

    session_id = events[0][1]["session_id"]
    assistant = None
    for _ in range(50):
        detail = client.get(
            f"/api/conversations/{session_id}", headers=auth_headers
        ).json()
        assistant = detail["messages"][-1]
        if assistant["routing_reasoning_content"]:
            break
        time.sleep(0.01)
    assert assistant is not None
    assert provider.route_reasoning_result in assistant["routing_reasoning_content"]
    assert assistant["router_model_name"] == "stub-router-1"


def test_stream_falls_back_to_final_state_and_persists_reply(
    client: TestClient, auth_headers, monkeypatch
) -> None:
    """A missing LangGraph custom stream must not erase a completed answer."""
    from app.routes import chat as chat_route

    class ValuesOnlyGraph:
        async def astream(self, state, *, context, stream_mode):
            assert stream_mode == ["custom", "values"]
            yield (
                "values",
                {
                    **state,
                    "final_response": "fallback reply",
                    "reasoning_content": "fallback reasoning",
                    "error": None,
                    "extracted_intent": "module_1",
                    "routed_by": "sticky",
                    "usage": {},
                },
            )

    monkeypatch.setattr(chat_route, "get_graph", lambda: ValuesOnlyGraph())
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hello"},
        headers=auth_headers,
    ) as response:
        events = sse_events("".join(response.iter_text()))

    assert [(name, data.get("text")) for name, data in events if name == "delta"] == [
        ("delta", "fallback reply")
    ]
    assert [
        data.get("text") for name, data in events if name == "reasoning_delta"
    ] == ["fallback reasoning"]
    assert events[-1] == ("persisted", {"saved": True})
    session_id = events[0][1]["session_id"]
    detail = client.get(
        f"/api/conversations/{session_id}", headers=auth_headers
    ).json()
    assert [(m["role"], m["content"]) for m in detail["messages"][-2:]] == [
        ("user", "hello"),
        ("assistant", "fallback reply"),
    ]
    assert detail["messages"][-1]["reasoning_content"] == "fallback reasoning"
    assert detail["revision"] >= 2


def test_stream_reports_provider_failure_in_band(client: TestClient, provider) -> None:
    provider.fail_with = ProviderError("died mid-stream")
    with client.stream("POST", "/api/chat/stream", json={"message": "hi"}) as response:
        # The HTTP status is already 200 by the time the failure happens, so the
        # error has to arrive as an event rather than a status code.
        assert response.status_code == 200
        events = sse_events("".join(response.iter_text()))

    errors = [d for n, d in events if n == "error"]
    assert errors and errors[0]["detail"] == "died mid-stream"
    assert not any(n == "done" for n, _ in events)


# ---------------------------------------------------------------------------
# Subject identity reaching the graph
# ---------------------------------------------------------------------------


def test_authenticated_subject_reaches_the_graph(
    client: TestClient, monkeypatch, memos, auth_headers
) -> None:
    """The caller's identity must arrive in AgentState, not stop at the route.

    Asserted through recall_memory_node's Memos call, since that is the first
    node that actually consumes `subject_id` — if the wiring in
    `_initial_state` were dropped, the node would see None and skip.
    """
    from app.routes import chat as chat_route

    monkeypatch.setattr(chat_route, "get_memos_manager", lambda _settings: memos)
    memos.retrieve_result = ["上次的困扰是失眠"]

    response = client.post("/api/chat", json={"message": "你好"}, headers=auth_headers)
    assert response.status_code == 200

    # The id handed to Memos is `user_profile.uuid` — server-minted, so the
    # test asserts its shape rather than a value it chose.
    assert len(memos.retrieve_calls) == 1
    assert len(memos.retrieve_calls[0]) == 36  # uuid4 with dashes


def test_unauthenticated_chat_works_but_does_not_recall(
    client: TestClient, monkeypatch, memos
) -> None:
    """Chat still answers a caller with no token — it just persists nothing."""
    from app.routes import chat as chat_route

    monkeypatch.setattr(chat_route, "get_memos_manager", lambda _settings: memos)

    response = client.post("/api/chat", json={"message": "你好"})
    assert response.status_code == 200
    assert response.json()["reply"]
    assert memos.retrieve_calls == []
