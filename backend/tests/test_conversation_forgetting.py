"""Deletion regressions using only isolated SQLite with foreign keys enabled."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import insert, select, event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.models import Conversation, ConversationMessage, AIExecutionEvent, ConversationModuleProgress
from app.v2_deletion import detach_conversation
from app.v2_repository import create_goal, start_cycle, append_memory, active_memories, initial_module


@pytest_asyncio.fixture
async def deletion_db():
    engine = create_async_engine("sqlite+aiosqlite://")
    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    async with engine.begin() as conn:
        await conn.run_sync(schema.create_all)
        for model in (Conversation, ConversationMessage, AIExecutionEvent, ConversationModuleProgress):
            await conn.run_sync(model.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await db.execute(insert(schema.tables["user_profile"]), [{"uuid":"a"},{"uuid":"b"}])
        await db.execute(insert(Conversation), [
            {"id":1,"session_id":"delete-me","subject_id":"a","title":"one"},
            {"id":2,"session_id":"keep-me","subject_id":"a","title":"two"},
            {"id":3,"session_id":"other-user","subject_id":"b","title":"three"}])
        await db.execute(insert(ConversationMessage), [
            {"id":n,"conversation_id":n,"position":0,"role":"user","content":"test"} for n in (1,2,3)])
        await db.commit()
        yield db, maker
    await engine.dispose()


async def erase(db):
    await detach_conversation(db, await db.get(Conversation, 1))


@pytest.mark.asyncio
async def test_exclusive_goal_cycle_and_memory_are_deleted(deletion_db):
    db, _ = deletion_db
    goal = await create_goal(db,user_id="a",title="erase goal",conversation_id=1)
    cycle = await start_cycle(db,user_id="a",goal_id=goal,conversation_id=1)
    await db.execute(insert(schema.tables["module_two_record"]), {"id":"plan","goal_id":goal,"version_no":1,"timezone":"Asia/Shanghai"})
    await db.execute(insert(schema.tables["module_three_record"]), {"id":"contract","goal_id":goal,"module_two_record_id":"plan","version_no":1})
    await db.execute(insert(schema.tables["module_four_record"]), {"id":"review","cycle_id":cycle})
    mine = await append_memory(db,user_id="a",memory_type="summary",content="erase memory",source_kind="ai_inference",source_message_id=1)
    other = await append_memory(db,user_id="a",memory_type="summary",content="keep memory",source_kind="ai_inference",source_message_id=2)
    alien = await append_memory(db,user_id="b",memory_type="summary",content="other user",source_kind="ai_inference",source_message_id=3)
    await db.execute(insert(schema.tables["conversation_runtime_states"]), {"conversation_id":1,"memory":{},"active_goal_id":goal,"active_cycle_id":cycle})
    await db.commit()
    await erase(db)
    await db.commit()
    for name, key in (("pa_goals",goal),("pa_cycles",cycle),("ba_memory",mine)):
        table=schema.tables[name]
        assert (await db.execute(select(table).where(table.c.id==key))).first() is None
    assert {m["id"] for m in await active_memories(db,user_id="a")} == {other}
    assert {m["id"] for m in await active_memories(db,user_id="b")} == {alien}
    assert (await db.execute(select(Conversation.id))).scalars().all() == [2,3]
    assert (await db.execute(select(ConversationMessage.id))).scalars().all() == [2,3]
    for name in ("module_two_record","module_three_record","module_four_record","pa_cycle_progress"):
        assert (await db.execute(select(schema.tables[name]))).first() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["runtime", "cycle", "unattributed_cycle"])
async def test_shared_goals_are_preserved(deletion_db, reference):
    db,_=deletion_db
    goal=await create_goal(db,user_id="a",title="shared",conversation_id=1)
    if reference=="runtime":
        await db.execute(insert(schema.tables["conversation_runtime_states"]), {"conversation_id":2,"memory":{},"active_goal_id":goal})
    else:
        await start_cycle(db,user_id="a",goal_id=goal,conversation_id=2 if reference=="cycle" else None)
    await erase(db)
    row=(await db.execute(select(schema.tables["pa_goals"]).where(schema.tables["pa_goals"].c.id==goal))).mappings().one()
    assert row["created_from_conversation_id"] is None and row["title"]=="shared"


@pytest.mark.asyncio
async def test_memory_replacement_chain_does_not_resurrect_deleted_fact(deletion_db):
    db,_=deletion_db
    old=await append_memory(db,user_id="a",memory_type="summary",content="old",source_kind="ai_inference",source_message_id=1)
    new=await append_memory(db,user_id="a",memory_type="summary",content="new",source_kind="ai_inference",source_message_id=2,supersedes_id=old)
    await erase(db)
    rows=await active_memories(db,user_id="a")
    assert len(rows)==1 and rows[0]["id"]==new and rows[0]["supersedes_id"] is None


@pytest.mark.asyncio
async def test_m1_evidence_is_forgotten_and_transaction_can_rollback(deletion_db):
    db,_=deletion_db
    await db.execute(insert(schema.tables["module_one_record"]), {"id":"m1","user_id":"a","version_no":1,"record_status":"confirmed","confirmation_status":"confirmed","confirmation_message_id":1})
    await db.execute(insert(schema.tables["user_module_one_state"]), {"user_id":"a","completed_steps":[],"status":"completed","completion_source":"user_confirmed","evidence_status":"available","confirmed_formulation_id":"m1"})
    await db.commit()
    await erase(db)
    assert await initial_module(db,user_id="a")=="module_1"
    await db.rollback()
    assert await initial_module(db,user_id="a")=="module_2"
    assert (await db.execute(select(Conversation.id).where(Conversation.id==1))).scalar_one()==1


@pytest.mark.asyncio
async def test_summary_has_source_and_cannot_write_after_delete(deletion_db,monkeypatch):
    db,maker=deletion_db
    from app.graph import nodes
    monkeypatch.setattr("app.v2_profile.enabled",lambda:True)
    monkeypatch.setattr(nodes,"save_ai_event",AsyncMock())
    provider=SimpleNamespace(name="stub",route_detailed=AsyncMock(return_value=SimpleNamespace(text="remember me",model="stub",usage={},request_id=None,finish_reason="stop")))
    args=dict(subject_id="a",settings_summarizer_max_tokens=100,transcript="test",from_module="module_1",to_module="module_2",sessionmaker=maker,session_id="delete-me")
    await nodes._summarize_and_save(provider,None,**args)
    row=(await active_memories(db,user_id="a"))[0]
    assert row["source_message_id"]==1
    await erase(db)
    await db.commit()
    await nodes._summarize_and_save(provider,None,**args)
    assert await active_memories(db,user_id="a")==[]


@pytest.mark.asyncio
async def test_endpoint_rejects_other_owner_and_resets_live_memory(deletion_db,monkeypatch):
    from fastapi import HTTPException
    from app.routes.conversations import delete_conversation
    from app.session import InMemorySessionStore
    db,_=deletion_db
    monkeypatch.setattr("app.v2_profile.enabled",lambda:True)
    store=InMemorySessionStore(ttl_seconds=60,max_messages=40)
    await store.get_or_create("delete-me")
    with pytest.raises(HTTPException) as error:
        await delete_conversation("delete-me",subject_id="b",db=db,store=store)
    assert error.value.status_code==404
    await delete_conversation("delete-me",subject_id="a",db=db,store=store)
    assert (await db.execute(select(Conversation.id).where(Conversation.id==1))).first() is None
    assert (await store.get_or_create("delete-me")).session_id != "delete-me"
