"""Member entry resumes one real conversation without discarding progress."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Conversation, ConversationRuntimeState, UserAccount
from app.opening import OPENING_MESSAGE_TEXT
from app.routes.conversations import current_conversation
from app.session import InMemorySessionStore


def _new(client, headers):
    response = client.post("/api/conversations", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _set_state(db_sessionmaker, session_id, *, module=None, memory=None,
               pinned=False, updated_at=None, title=None):
    async def save():
        async with db_sessionmaker() as db:
            conversation = (await db.execute(select(Conversation).where(
                Conversation.session_id == session_id))).scalar_one()
            conversation.pinned = pinned
            if updated_at is not None:
                conversation.updated_at = updated_at
            if title is not None:
                conversation.title = title
            runtime = await db.get(ConversationRuntimeState, conversation.id)
            if module is not None:
                runtime.module = module
            if memory is not None:
                runtime.memory = memory
            await db.commit()
    asyncio.run(save())


def test_current_requires_authentication(client):
    assert client.post("/api/conversations/current").status_code == 401


def test_first_entry_creates_one_opening_and_starts_m1(client, auth_headers, provider):
    first = client.post("/api/conversations/current", headers=auth_headers)
    repeat = client.post("/api/conversations/current", headers=auth_headers)
    assert first.status_code == repeat.status_code == 200
    assert repeat.json() == first.json()
    assert [item["content"] for item in first.json()["messages"]] == [OPENING_MESSAGE_TEXT]
    assert len(client.get("/api/conversations", headers=auth_headers).json()) == 1
    assert provider.seen == []

    reply = client.post("/api/chat", headers=auth_headers, json={
        "session_id": first.json()["session_id"], "message": "你好"})
    assert reply.status_code == 200, reply.text
    assert reply.json()["reply_module"] == "module_1"


def test_resume_uses_latest_chat_not_pin_and_preserves_progress(
    client, auth_headers, db_sessionmaker,
):
    old, latest = _new(client, auth_headers), _new(client, auth_headers)
    now = datetime.now(timezone.utc)
    _set_state(db_sessionmaker, old["session_id"], pinned=True,
               updated_at=now - timedelta(days=1))
    _set_state(db_sessionmaker, latest["session_id"], module="module_3",
               memory={"progress_marker": "preserve"}, updated_at=now)
    response = client.post("/api/conversations/current", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["session_id"] == latest["session_id"]
    assert response.json()["next_module"] == "module_3"
    assert response.json()["messages"] == latest["messages"]
    rows = client.get("/api/conversations", headers=auth_headers).json()
    assert len(rows) == 2
    assert rows[0]["session_id"] == old["session_id"]  # Admin listing is unchanged.


def test_sandbox_is_excluded_by_durable_marker_even_after_rename(
    client, auth_headers, db_sessionmaker,
):
    real, sandbox = _new(client, auth_headers), _new(client, auth_headers)
    now = datetime.now(timezone.utc)
    _set_state(db_sessionmaker, real["session_id"], updated_at=now,
               title="沙盒 · 是我给真实对话起的标题")
    _set_state(db_sessionmaker, sandbox["session_id"],
               updated_at=now + timedelta(seconds=1), title="重新命名的沙盒",
               memory={"sandbox_mode": "true", "sandbox_start_module": "module_4"})
    response = client.post("/api/conversations/current", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["session_id"] == real["session_id"]
    assert len(client.get("/api/conversations", headers=auth_headers).json()) == 2


def test_only_sandbox_creates_one_real_chat_and_preserves_sandbox(
    client, auth_headers, db_sessionmaker,
):
    sandbox = _new(client, auth_headers)
    _set_state(db_sessionmaker, sandbox["session_id"], module="module_4",
               memory={"sandbox_mode": True})
    first = client.post("/api/conversations/current", headers=auth_headers).json()
    repeat = client.post("/api/conversations/current", headers=auth_headers).json()
    assert first["session_id"] != sandbox["session_id"]
    assert repeat["session_id"] == first["session_id"]
    assert len(client.get("/api/conversations", headers=auth_headers).json()) == 2
    assert client.get(f"/api/conversations/{sandbox['session_id']}",
                      headers=auth_headers).json()["next_module"] == "module_4"


def test_current_cannot_resume_another_users_chat(client, register):
    alice, bob = register(username="alice"), register(username="bob")
    first = client.post("/api/conversations/current", headers=alice).json()
    second = client.post("/api/conversations/current", headers=bob).json()
    assert first["session_id"] != second["session_id"]
    assert client.post("/api/conversations/current", headers=alice).json()["session_id"] == first["session_id"]
    assert client.get(f"/api/conversations/{first['session_id']}", headers=bob).status_code == 404


def test_explicit_create_still_creates_separate_conversations(client, auth_headers):
    first, second = _new(client, auth_headers), _new(client, auth_headers)
    assert first["session_id"] != second["session_id"]
    assert len(client.get("/api/conversations", headers=auth_headers).json()) == 2


@pytest.mark.asyncio
async def test_simultaneous_first_entries_share_one_chat_across_connections(tmp_path):
    # A file database gives every request an independent connection and lock;
    # the ordinary in-memory HTTP fixture intentionally shares one connection.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            db.add(UserAccount(id=1, username="member", password_hash="unused",
                               profile_uuid="member"))
            await db.commit()
        ready, start = asyncio.Queue(), asyncio.Event()

        async def enter():
            # Distinct stores model two API workers, rather than relying on
            # an in-process lock to make the test pass.
            store = InMemorySessionStore(ttl_seconds=60, max_messages=40)
            async with maker() as db:
                await db.execute(select(UserAccount.id))  # Authentication read.
                await ready.put(True)
                await start.wait()
                return await current_conversation(
                    caller=SimpleNamespace(account=SimpleNamespace(id=1), subject_id="member"),
                    db=db, store=store,
                )

        requests = [asyncio.create_task(enter()) for _ in range(4)]
        for _ in requests:
            await ready.get()
        start.set()
        results = await asyncio.gather(*requests)
        assert len({result.session_id for result in results}) == 1
        assert all(len(result.messages) == 1 for result in results)
        async with maker() as db:
            assert len((await db.execute(select(Conversation.id))).all()) == 1
    finally:
        await engine.dispose()


def test_v2_entry_starts_m1_and_resume_keeps_module_and_memory():
    # V2 ORM column names are selected at import time, requiring a fresh process.
    script = r'''
import asyncio
from types import SimpleNamespace
from app.config import Settings, get_settings
Settings.model_config['env_file'] = None
get_settings.cache_clear()
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database_v2_schema import metadata
from app.models import AccountSettings, Conversation, ConversationMessage, ConversationReplySettings, UserAccount
from app.routes.conversations import current_conversation
from app.session import InMemorySessionStore

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        # Include app-owned reply preferences as production Base.metadata.create_all does.
        for model in (Conversation, ConversationMessage, UserAccount, AccountSettings, ConversationReplySettings):
            await conn.run_sync(model.__table__.create)
    try:
        async with maker() as db:
            await db.execute(insert(metadata.tables['user_profile']), {'uuid': 'member'})
            await db.execute(insert(UserAccount), {
                'id': 1, 'username': 'member', 'password_hash': 'unused', 'profile_uuid': 'member'})
            await db.commit()
            caller = SimpleNamespace(subject_id='member', account=SimpleNamespace(id=1))
            store = InMemorySessionStore(ttl_seconds=60, max_messages=40)
            first = await current_conversation(caller=caller, db=db, store=store)
            assert first.next_module == 'module_1'
            assert len(first.messages) == 1
            runtime = metadata.tables['conversation_runtime_states']
            await db.execute(update(runtime).values(current_module='module_4',
                memory={'progress_marker': 'preserve'}))
            await db.commit()
            resumed = await current_conversation(caller=caller, db=db, store=store)
            assert resumed.session_id == first.session_id
            assert resumed.next_module == 'module_4'
            assert resumed.messages == first.messages
            assert (await db.execute(select(runtime.c.memory))).scalar_one() == {'progress_marker': 'preserve'}
    finally:
        await engine.dispose()
asyncio.run(main())
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_SCHEMA_VERSION": "v2",
             "DATABASE_URL": "sqlite+aiosqlite:///:memory:"},
        capture_output=True, text=True, encoding="utf-8", timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
