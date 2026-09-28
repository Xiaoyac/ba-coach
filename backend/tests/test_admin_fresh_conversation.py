"""Admin new-chat starts a distinct M1 without reusing or deleting old evidence."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.m1_contract import VERSION
from app.models import AccountSettings, ConversationMessage, UserAccount
from app.opening import OPENING_MESSAGE_TEXT
from app.pre_reply_routing import load_routing_snapshot
from app.program_confirmation import draft
from app.v2_workflow import clinical_context, load_workflow, persist_record, record_steps, runtime_for
from app.workflow_contract import MODULE_STEP_KEYS
from test_goal_overview import goal_api
from test_m1_program_0914 import seed


def test_admin_explicit_new_chat_starts_m1_and_seeds_real_opening(client, register, db_sessionmaker, provider):
    headers = register(username="freshadmin")

    async def promote():
        async with db_sessionmaker() as db:
            account_id = await db.scalar(select(UserAccount.id).where(UserAccount.username == "freshadmin"))
            await db.execute(update(AccountSettings).where(AccountSettings.account_id == account_id).values(role="admin"))
            await db.commit()
    asyncio.run(promote())
    first = client.post("/api/conversations", headers=headers).json()
    second = client.post("/api/conversations", headers=headers).json()
    assert first["session_id"] != second["session_id"]
    for created in (first, second):
        assert created["next_module"] == "module_1"
        assert [m["content"] for m in created["messages"]] == [OPENING_MESSAGE_TEXT]
        assert client.get(f"/api/conversations/{created['session_id']}", headers=headers).json() == created
    response = client.post("/api/chat", headers=headers, json={
        "session_id": second["session_id"], "message": "你好"})
    assert response.status_code == 200, response.text
    assert response.json()["reply_module"] == "module_1"
    assert provider.seen[-1][0].content == OPENING_MESSAGE_TEXT


@pytest.mark.parametrize("completion_source", ["legacy_imported", "user_confirmed"])
def test_v2_admin_creation_ignores_reusable_m1_but_member_creation_keeps_it(completion_source):
    # The V2 runtime ORM column names are selected at import, so isolate that
    # configuration from legacy HTTP fixtures in a fresh process.
    script = r'''
import asyncio, os
from app.config import Settings, get_settings
Settings.model_config['env_file'] = None
get_settings.cache_clear()
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database_v2_schema import metadata
from app.models import AccountSettings, Conversation, ConversationMessage, UserAccount
from app.opening import OPENING_MESSAGE_TEXT
from app.routes.conversations import create_conversation
from app.session import InMemorySessionStore

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        for model in (Conversation, ConversationMessage, UserAccount, AccountSettings):
            await conn.run_sync(model.__table__.create)
    try:
        async with maker() as db:
            for account_id, role in ((1, 'admin'), (2, 'user')):
                await db.execute(insert(metadata.tables['user_profile']), {'uuid': role, 'module1_done_flag': True})
                await db.execute(insert(UserAccount), {'id': account_id, 'username': role,
                    'password_hash': 'unused', 'profile_uuid': role})
                await db.execute(insert(AccountSettings), {'account_id': account_id, 'role': role})
                source = os.environ['TEST_COMPLETION_SOURCE']
                confirmed = source == 'user_confirmed'
                if confirmed:
                    await db.execute(insert(metadata.tables['module_one_record']), {
                        'id': role + '-confirmed', 'user_id': role, 'version_no': 1,
                        'record_status': 'confirmed', 'confirmation_status': 'confirmed', 'confirmation_message_id': 999})
                await db.execute(insert(metadata.tables['user_module_one_state']), {'user_id': role,
                    'completed_steps': ['goal_setting_consent'], 'status': 'completed', 'completion_source': source,
                    'evidence_status': 'available' if confirmed else 'missing',
                    'confirmed_formulation_id': role + '-confirmed' if confirmed else None})
                await db.execute(insert(metadata.tables['pa_goals']), {
                    'id': role + '-goal', 'user_id': role, 'title': 'Historical goal', 'status': 'active'})
            await db.commit()
            store = InMemorySessionStore(ttl_seconds=60, max_messages=40)
            one = await create_conversation(subject_id='admin', db=db, store=store)
            two = await create_conversation(subject_id='admin', db=db, store=store)
            member = await create_conversation(subject_id='user', db=db, store=store)
            assert one.session_id != two.session_id
            for created in (one, two):
                assert created.next_module == 'module_1'
                assert [message.content for message in created.messages] == [OPENING_MESSAGE_TEXT]
                session = await store.get(created.session_id)
                assert session.module == 'module_1'
            assert member.next_module == 'module_2'
            assert member.messages[0].content != OPENING_MESSAGE_TEXT
            runtime = metadata.tables['conversation_runtime_states']
            rows = (await db.execute(select(runtime))).mappings().all()
            assert all(row['active_goal_id'] is None and row['active_cycle_id'] is None for row in rows)
            assert sum(row['memory'].get('fresh_m1') is True for row in rows) == 2
            states = (await db.execute(select(metadata.tables['user_module_one_state']))).mappings().all()
            assert all(row['status'] == 'completed' and row['completed_steps'] == ['goal_setting_consent'] for row in states)
            assert len((await db.execute(select(metadata.tables['pa_goals']))).all()) == 2
    finally:
        await engine.dispose()
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_SCHEMA_VERSION": "v2", "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
             "TEST_COMPLETION_SOURCE": completion_source}, capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


async def _fresh_runtime(db):
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(
        current_module="module_1", memory={"fresh_m1": True}))
    table = schema.tables["user_module_one_state"]
    await db.execute(update(table).where(table.c.user_id == "a").values(
        status="completed", completion_source="legacy_imported", evidence_status="missing",
        completed_steps=list(MODULE_STEP_KEYS["module_1"])))
    await db.commit()


@pytest.mark.asyncio
async def test_new_m1_does_not_read_or_rewrite_past_completion_or_draft(goal_api):
    _, db, _ = goal_api
    await seed(db, source="different-chat")
    await _fresh_runtime(db)
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    before = dict((await db.execute(select(schema.tables["user_module_one_state"])
        .where(schema.tables["user_module_one_state"].c.user_id == "a"))).mappings().one())
    steps, cycle = await load_workflow(maker, "chat-a")
    assert steps["module_1"] == [] and cycle is None
    context = SimpleNamespace(settings=SimpleNamespace(database_schema_version="v2"), sessionmaker=maker)
    snapshot = await load_routing_snapshot({"session_id": "chat-a", "subject_id": "a"}, context)
    assert snapshot["routing_state"]["m1_status"] == "in_progress"
    _, state = await runtime_for(db, "chat-a")
    assert await draft(db, state, "a") is None
    assert not any("M1 历史状态" in line or "问题理解记录" in line for line in await clinical_context(maker, "a", "chat-a"))
    from app.knowledge_context import assemble_knowledge_context
    mediator = await assemble_knowledge_context(maker, "a", "chat-a",
        module="module_1", task="m1_relationship")
    assert mediator["facts"] == []
    target, _ = await record_steps(db, session_id="chat-a", user_id="a", module="module_1",
        requested_target="module_2", steps=list(MODULE_STEP_KEYS["module_1"]), assistant_message_id=20)
    assert target == "module_1"
    after = dict((await db.execute(select(schema.tables["user_module_one_state"])
        .where(schema.tables["user_module_one_state"].c.user_id == "a"))).mappings().one())
    assert after == before
    await db.commit()
    new_id = await persist_record(maker, module="module_1", user_id="a", cycle_id=None, data={
        "chief_complaint": "This conversation", "m1_contract": {"version": VERSION,
        "session_id": "chat-a", "assistant_message_id": 20, "completed_steps": [],
        "missing_fields": ["m1_milestone_1"]}})
    assert new_id and new_id != "m1-draft"
    old = (await db.execute(select(schema.tables["module_one_record"])
        .where(schema.tables["module_one_record"].c.id == "m1-draft"))).mappings().one()
    assert old["event_experience"]["_m1_contract"]["session_id"] == "different-chat"


@pytest.mark.asyncio
async def test_old_m1_tab_cannot_overwrite_a_new_fresh_conversation_draft(goal_api):
    _, db, _ = goal_api
    await seed(db)
    await _fresh_runtime(db)
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 2).values(current_module="module_1"))
    await db.execute(insert(ConversationMessage), {"id": 30, "conversation_id": 2,
        "position": 1, "role": "assistant", "content": "Old chat response"})
    await db.commit()
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    created = await persist_record(maker, module="module_1", user_id="a", cycle_id=None, data={
        "chief_complaint": "Old chat facts", "m1_contract": {"version": VERSION,
        "session_id": "new-chat-a", "assistant_message_id": 30, "completed_steps": [],
        "missing_fields": ["m1_milestone_1"]}})
    assert created and created != "m1-draft"
    records = schema.tables["module_one_record"]
    protected = await db.scalar(select(records.c.event_experience).where(records.c.id == "m1-draft"))
    assert protected["_m1_contract"]["session_id"] == "chat-a"
    _, state = await runtime_for(db, "chat-a")
    assert (await draft(db, state, "a"))["id"] == "m1-draft"


@pytest.mark.asyncio
async def test_new_m1_can_confirm_current_evidence_and_leave_fresh_mode(goal_api):
    _, db, _ = goal_api
    await seed(db)
    await _fresh_runtime(db)
    text = "我理解了，愿意开始目标设定。"
    await db.execute(insert(ConversationMessage), {"id": 19, "conversation_id": 1,
        "position": 0, "role": "user", "content": text})
    records = schema.tables["module_one_record"]
    event = await db.scalar(select(records.c.event_experience).where(records.c.id == "m1-draft"))
    event["_m1_contract"]["evidence"] = {"consent": {"role": "user", "quote": text, "turn": 0}}
    await db.execute(update(records).where(records.c.id == "m1-draft").values(event_experience=event))
    target, cycle = await record_steps(db, session_id="chat-a", user_id="a", module="module_1",
        requested_target="module_2", steps=[], assistant_message_id=20)
    assert target == "module_2" and cycle is None
    _, state = await runtime_for(db, "chat-a")
    assert state["current_module"] == "module_2" and "fresh_m1" not in state["memory"]
    assert state["active_goal_id"] is None
    completion = (await db.execute(select(schema.tables["user_module_one_state"])
        .where(schema.tables["user_module_one_state"].c.user_id == "a"))).mappings().one()
    assert completion["confirmed_formulation_id"] == "m1-draft"
    assert completion["completed_steps"] == list(MODULE_STEP_KEYS["module_1"])
    await db.commit()
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    context = SimpleNamespace(settings=SimpleNamespace(database_schema_version="v2"), sessionmaker=maker)
    snapshot = await load_routing_snapshot({"session_id": "chat-a", "subject_id": "a"}, context)
    assert snapshot["routing_state"]["m1_status"] == "completed"
