"""Sharing is explicit publication of one immutable transcript.

Use isolated databases and stub models. No model is called by a share request.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.graph import nodes
from app.models import AIExecutionEvent, Conversation, ConversationMessage, ConversationShare
from scripts.create_conversation_shares import migrate


def run(awaitable):
    return asyncio.get_event_loop().run_until_complete(awaitable)


def start(client, headers, message="分享这一段对话"):
    response = client.post("/api/chat", json={"message": message}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def publish(client, headers, session_id):
    response = client.post(f"/api/conversations/{session_id}/shares", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_snapshot_copies_only_saved_panels_and_local_message_ids(
    client, auth_headers, db_sessionmaker, provider,
):
    # Allocate unrelated private rows first so snapshot IDs cannot accidentally
    # be mistaken for copied live IDs.
    foreign_session = start(client, auth_headers, "OTHER_CONVERSATION_SECRET")
    session_id = start(client, auth_headers)
    private_detail = client.get(f"/api/conversations/{session_id}", headers=auth_headers).json()
    assistant_id = private_detail["messages"][-1]["id"]
    knowledge = {
        "version": 4, "available": True, "module": "module_2",
        "mediator_guidance": "结合当前活动进行解释。",
        "mediator_reasoning_content": "已保存的中介思考",
        "mediator_model": "mediator-test", "mediator_duration_ms": 45,
        "mediator_decision": "use", "mediator_status": "completed",
        "mediator_selections": [{"id": "chunk-one", "quote": "行动", "application": "用于解释"}],
        "recalled": [{"id": "chunk-one", "source": "BA", "text": "行动可以帮助情绪", "score": 0.9}],
        "provided": [{"id": "chunk-one", "source": "BA", "text": "行动可以帮助情绪", "score": 0.9}],
        "internal_secret": "UNRELATED_REFERENCE_METADATA",
    }

    async def arrange():
        async with db_sessionmaker() as db:
            message = await db.get(ConversationMessage, assistant_id)
            message.reasoning_content = "已保存的回复思考"
            message.routing_reasoning_content = "已保存的路由思考"
            message.router_model_name = "router-test"
            message.main_generation_duration_ms = 200
            message.router_duration_ms = 80
            event = (await db.execute(select(AIExecutionEvent).where(
                AIExecutionEvent.assistant_message_id == assistant_id,
                AIExecutionEvent.stage == "main_generation",
            ))).scalar_one()
            event.event_metadata = {
                "knowledge_references": knowledge,
                "main_input": "FULL_PROMPT_SECRET", "memory": "ACCOUNT_MEMORY_SECRET",
            }
            await db.commit()

    run(arrange())
    calls = (len(provider.seen), len(provider.route_calls))
    shared = publish(client, auth_headers, session_id)
    response = client.get(f"/api/shares/{shared['token']}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in response.headers["x-robots-tag"]
    data = response.json()
    assert set(data) == {"snapshot_version", "title", "created_at", "messages"}
    assert [m["id"] for m in data["messages"]] == list(range(1, len(data["messages"]) + 1))
    message = data["messages"][-1]
    assert message["id"] != assistant_id
    assert message["reasoning_content"] == "已保存的回复思考"
    assert message["routing_reasoning_content"] == "已保存的路由思考"
    assert message["model_name"] == "stub-1" and message["router_model_name"] == "router-test"
    assert message["timing"]["reply_generation_ms"] == 200
    assert message["knowledge_references"]["mediator_guidance"] == knowledge["mediator_guidance"]
    assert message["knowledge_references"]["mediator_reasoning_content"] == knowledge["mediator_reasoning_content"]
    assert message["knowledge_references"]["provided"][0]["text"] == "行动可以帮助情绪"
    for secret in (session_id, foreign_session, "FULL_PROMPT_SECRET", "ACCOUNT_MEMORY_SECRET",
                   "OTHER_CONVERSATION_SECRET", "UNRELATED_REFERENCE_METADATA"):
        assert secret not in response.text
    assert (len(provider.seen), len(provider.route_calls)) == calls
    assert client.get(f"/api/conversations/{session_id}").status_code == 401
    assert client.get(f"/api/conversations/messages/{assistant_id}/knowledge").status_code == 401

    async def stored():
        async with db_sessionmaker() as db:
            share = await db.get(ConversationShare, shared["id"])
            assert share.token_digest == hashlib.sha256(shared["token"].encode()).hexdigest()
            assert shared["token"] not in json.dumps(share.snapshot)
    run(stored())


def test_share_stays_frozen_after_new_turn_title_and_detail_changes(
    client, auth_headers, db_sessionmaker,
):
    session_id = start(client, auth_headers)
    shared = publish(client, auth_headers, session_id)
    public_path = f"/api/shares/{shared['token']}"
    before = client.get(public_path).json()
    assistant_id = client.get(f"/api/conversations/{session_id}", headers=auth_headers).json()["messages"][-1]["id"]
    assert client.patch(f"/api/conversations/{session_id}", headers=auth_headers,
                        json={"title": "改名之后"}).status_code == 200
    assert client.post("/api/chat", headers=auth_headers,
                       json={"session_id": session_id, "message": "NEW_PRIVATE_MESSAGE"}).status_code == 200

    async def alter_trace():
        async with db_sessionmaker() as db:
            message = await db.get(ConversationMessage, assistant_id)
            message.reasoning_content = "LATER_REASONING"
            await db.commit()
    run(alter_trace())
    assert client.get(public_path).json() == before
    later = publish(client, auth_headers, session_id)
    later_snapshot = client.get(f"/api/shares/{later['token']}").json()
    assert later_snapshot["title"] == "改名之后"
    assert len(later_snapshot["messages"]) == len(before["messages"]) + 2


def test_owner_only_creation_legacy_invalidation_and_delete(client, auth_headers, register, db_sessionmaker):
    # Production enables these on every SQLite connection; the generic test
    # fixture omits them. Exercise real cascading deletion in this test too.
    async def enable_foreign_keys():
        async with db_sessionmaker() as db:
            await db.execute(text("PRAGMA foreign_keys=ON"))
    run(enable_foreign_keys())
    other = register(username="othershareowner")
    session_id = start(client, auth_headers)
    path = f"/api/conversations/{session_id}/shares"
    assert client.post(path).status_code == 401
    assert client.post(path, headers=other).status_code == 404
    shared = publish(client, auth_headers, session_id)
    public_path = f"/api/shares/{shared['token']}"
    assert client.get(public_path, headers=other).status_code == 200
    # Removed history/revocation routes must not remain callable.
    assert client.get(path, headers=auth_headers).status_code == 405
    assert client.delete(f"{path}/{shared['id']}", headers=auth_headers).status_code == 404
    assert "revoked_at" not in shared
    assert client.get(public_path).status_code == 200

    # Old invalidated links must not become public again after this cleanup.
    async def legacy_invalidation():
        async with db_sessionmaker() as db:
            share = await db.get(ConversationShare, shared["id"])
            share.revoked_at = datetime.now(timezone.utc)
            await db.commit()
    run(legacy_invalidation())
    assert client.get(public_path).status_code == 404
    second = publish(client, auth_headers, session_id)
    assert client.delete(f"/api/conversations/{session_id}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/shares/{second['token']}").status_code == 404
    async def deleted():
        async with db_sessionmaker() as db:
            assert await db.get(ConversationShare, shared["id"]) is None
            assert await db.get(ConversationShare, second["id"]) is None
    run(deleted())


@pytest.mark.parametrize("token", ["invalid", "a" * 43, "a" * 42 + "!", "a" * 44])
def test_missing_or_malformed_share_does_not_disclose_state(client, token):
    response = client.get(f"/api/shares/{token}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Share not found"}
    assert response.headers["cache-control"] == "private, no-store"


def test_missing_historical_details_are_unavailable_not_regenerated(client, auth_headers, provider):
    created = client.post("/api/conversations", headers=auth_headers).json()
    shared = publish(client, auth_headers, created["session_id"])
    data = client.get(f"/api/shares/{shared['token']}").json()
    assert len(data["messages"]) == 1
    message = data["messages"][0]
    assert message["reasoning_content"] is None and message["routing_reasoning_content"] is None
    assert message["knowledge_references"]["available"] is False
    assert message["knowledge_references"]["provided"] == []
    assert provider.seen == [] and provider.route_calls == []


@pytest.mark.parametrize("busy", ["turn", "background", "unanswered"])
def test_incomplete_conversation_cannot_be_published(
    client, auth_headers, store, db_sessionmaker, monkeypatch, busy,
):
    session_id = start(client, auth_headers)
    lock = run(store.get_turn_lock(session_id))
    if busy == "turn":
        run(lock.acquire())
    elif busy == "background":
        monkeypatch.setitem(nodes._routing_tasks, session_id, SimpleNamespace(done=lambda: False))
    else:
        async def add_unanswered():
            async with db_sessionmaker() as db:
                conversation = (await db.execute(select(Conversation).where(
                    Conversation.session_id == session_id))).scalar_one()
                db.add(ConversationMessage(conversation_id=conversation.id,
                    position=conversation.messages[-1].position + 1, role="user", content="still generating"))
                await db.commit()
        run(add_unanswered())
    try:
        response = client.post(f"/api/conversations/{session_id}/shares", headers=auth_headers)
        assert response.status_code == 409
        async def no_snapshot():
            async with db_sessionmaker() as db:
                shares = (await db.execute(select(ConversationShare).join(Conversation).where(
                    Conversation.session_id == session_id,
                ))).scalars().all()
                assert shares == []
        run(no_snapshot())
    finally:
        if busy == "turn":
            lock.release()


async def test_migration_is_additive_dry_by_default_and_repeatable(tmp_path):
    path = str(tmp_path / "shares-migration.db")
    url = f"sqlite+aiosqlite:///{path}"
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE conversations (id INTEGER PRIMARY KEY, title TEXT)"))
            await connection.execute(text("INSERT INTO conversations VALUES (1, 'kept')"))
        plan = await migrate(url, expected_database=path)
        assert len(plan) == 2
        assert all("CREATE " in statement for statement in plan)
        async with engine.begin() as connection:
            assert await connection.run_sync(lambda c: inspect(c).get_table_names()) == ["conversations"]
        assert await migrate(url, expected_database=path, apply=True) == plan
        assert await migrate(url, expected_database=path, apply=True) == []
        async with engine.begin() as connection:
            assert await connection.run_sync(lambda c: inspect(c).get_table_names()) == ["conversation_shares", "conversations"]
            assert (await connection.execute(text("SELECT title FROM conversations WHERE id=1"))).scalar_one() == "kept"
        with pytest.raises(RuntimeError, match="explicit expected target"):
            await migrate(url, expected_database="different", apply=True)
    finally:
        await engine.dispose()


async def test_migration_refuses_incompatible_existing_table(tmp_path):
    path = str(tmp_path / "shares-incompatible.db")
    url = f"sqlite+aiosqlite:///{path}"
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE conversations (id INTEGER PRIMARY KEY)"))
            await connection.execute(text("CREATE TABLE conversation_shares (id INTEGER PRIMARY KEY)"))
        with pytest.raises(RuntimeError, match="differs from expected schema"):
            await migrate(url, expected_database=path, apply=True)
    finally:
        await engine.dispose()
