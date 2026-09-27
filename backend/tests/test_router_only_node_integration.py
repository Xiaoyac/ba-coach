"""Router-only reply generation and post-reply facts keep separate authorities."""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys

import pytest
from langgraph.runtime import Runtime

from app import reply_workflow
from app.graph import nodes
from app.providers.base import Completion, StreamDelta, as_text


CARD = {"text": "程序生成的目标确认卡", "record_id": "draft", "record_hash": "v1"}


@pytest.mark.parametrize("mode", ["router_only", "router_code", None])
@pytest.mark.parametrize("stream", [False, True])
async def test_only_router_uses_main_model_instead_of_confirmation_card(context, provider, monkeypatch, mode, stream):
    async def authority(*args):
        return {"available": True, "current_module": "module_2", "flow_status": "active",
                "plan_confirmed": False, "confirmation_summary": CARD}

    monkeypatch.setattr(reply_workflow, "read_reply_workflow", authority)
    events = []
    monkeypatch.setattr(nodes, "_emit", events.append)
    ctx = replace(context, stream=stream,
        settings=context.settings.model_copy(update={"database_schema_version": "v2"}))
    result = await nodes.MODULE_NODES["module_2"]({
        "session_id": "owned-admin", "subject_id": "admin-profile", "user_input": "好的",
        "routing_mode": mode, "memory": {"routing_mode": mode or "router_only"},
        "clinical_context": ['本周期绑定计划：{"record_status":"draft"}'],
    }, Runtime(context=ctx))
    if mode != "router_only":
        assert result["final_response"] == CARD["text"]
        assert result["provider"] == "workflow_state"
        assert provider.seen == []
    else:
        assert result["final_response"] == ("hello" if stream else "saw 1 messages")
        assert result["provider"] == "stub"
        assert len(provider.seen) == 1
        compiled = as_text(provider.systems[0])
        assert "当前模块已由 Router 选择并提交" in compiled
        assert "不是继续对话的前提" in compiled
        assert '"plan_confirmed": false' in compiled
        assert '"record_status":"draft"' in compiled
        assert CARD["text"] not in compiled
        assert "rendered_confirmation" not in result["telemetry"]
    if stream:
        assert "".join(event.get("text", "") for event in events if event["type"] == "delta") == result["final_response"]


@pytest.mark.parametrize("reply,code", [
    ("计划已保存。", "uncommitted_workflow_claim"),
    ("你就是懒。", "shaming_prescription"),
])
@pytest.mark.parametrize("stream", [False, True])
async def test_only_router_still_checks_real_save_claims_and_safety(context, provider, monkeypatch, reply, code, stream):
    async def authority(*args):
        return {"available": True, "current_module": "module_3", "plan_confirmed": False}

    async def generated(**kwargs):
        return Completion(text=reply, model="synthetic")

    async def streamed(**kwargs):
        yield StreamDelta(kind="content", text=reply)

    monkeypatch.setattr(reply_workflow, "read_reply_workflow", authority)
    monkeypatch.setattr(provider, "complete", generated)
    monkeypatch.setattr(provider, "stream", streamed)
    events = []
    monkeypatch.setattr(nodes, "_emit", events.append)
    ctx = replace(context, stream=stream,
        settings=context.settings.model_copy(update={"database_schema_version": "v2"}))
    result = await nodes.MODULE_NODES["module_3"]({
        "session_id": "owned-admin", "subject_id": "admin-profile", "user_input": "继续吧",
        "routing_mode": "router_only",
    }, Runtime(context=ctx))
    assert result["final_response"] != reply
    assert any(finding["code"] == code for finding in result["telemetry"]["answer_validator"]["findings"])
    if stream:
        assert reply not in "".join(event.get("text", "") for event in events if event["type"] == "delta")


def test_only_router_business_facts_do_not_reinstate_progress_gates():
    prompt = reply_workflow.workflow_prompt({"available": True, "current_module": "module_4",
        "flow_status": "waiting_execution", "plan_confirmed": False,
        "readiness": {"missing_fields": ["internal_missing_key"]},
        "confirmation_summary": CARD}, routing_mode="router_only")
    assert "internal_missing_key" not in prompt
    assert CARD["text"] not in prompt
    assert "模块切换本身不表示目标、计划或记录已经保存、确认或执行" in prompt
    assert '"current_module": "module_4"' in prompt
    assert '"plan_confirmed": false' in prompt


async def test_router_selected_module_can_be_described_without_claiming_plan_confirmation(context, provider, monkeypatch):
    async def authority(*args):
        return {"available": True, "current_module": "module_3", "plan_confirmed": False}

    async def generated(**kwargs):
        return Completion(text="现在进入模块三。", model="synthetic")

    monkeypatch.setattr(reply_workflow, "read_reply_workflow", authority)
    monkeypatch.setattr(provider, "complete", generated)
    ctx = replace(context, settings=context.settings.model_copy(update={"database_schema_version": "v2"}))
    result = await nodes.MODULE_NODES["module_3"]({
        "session_id": "owned-admin", "subject_id": "admin-profile", "user_input": "继续吧",
        "routing_mode": "router_only",
    }, Runtime(context=ctx))
    assert result["final_response"] == "现在进入模块三。"
    assert result["telemetry"]["answer_validator"]["status"] == "passed"
    assert not result["reply_held"]


# A fresh process selects the V2 ORM at import time and never reads a local .env.
BACKGROUND = r'''
import asyncio, sys
from app.config import Settings, get_settings
Settings.model_config['env_file'] = None
get_settings.cache_clear()
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database_v2_schema import metadata
from app.models import Conversation, ConversationMessage, AIExecutionEvent, UserAccount, AccountSettings
from app.graph import nodes
from app.graph.state import GraphContext
from app.retrieval import StubKnowledgeBase
from app.session import InMemorySessionStore
from app import v2_workflow
sys.path.insert(0, 'tests')
from conftest import StubProvider

async def main():
    module, stored_mode, role = sys.argv[1:]
    only = stored_mode == 'router_only' and role == 'admin'
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            for model in (Conversation, ConversationMessage, AIExecutionEvent, UserAccount, AccountSettings):
                await conn.run_sync(model.__table__.create)
        rt = metadata.tables['conversation_runtime_states']
        memory = {'routing_mode': stored_mode, 'freshness_marker': 'durable-only'}
        async with maker() as db:
            await db.execute(insert(metadata.tables['user_profile']), {'uuid': 'synthetic'})
            await db.execute(insert(UserAccount), {'id': 1, 'username': 'fixture', 'profile_uuid': 'synthetic', 'password_hash': 'unused'})
            await db.execute(insert(AccountSettings), {'account_id': 1, 'role': role})
            await db.execute(insert(Conversation), {'id': 1, 'session_id': 'single', 'subject_id': 'synthetic', 'revision': 2})
            await db.execute(insert(ConversationMessage), [
                {'id': 1, 'conversation_id': 1, 'position': 0, 'role': 'user', 'content': '我最近写报告拖延'},
                {'id': 2, 'conversation_id': 1, 'position': 1, 'role': 'assistant', 'content': '愿意说说最近一次吗？'}])
            await db.execute(insert(rt), {'conversation_id': 1, 'current_module': module, 'memory': memory})
            await db.commit()
        calls = {'extract': 0, 'record_steps': 0, 'set_module': 0}
        async def extract(*args, **kwargs):
            calls['extract'] += 1
            return {'chief_complaint': '写报告拖延'} if module == 'module_1' else {}
        async def steps(*args, **kwargs):
            calls['record_steps'] += 1
            assert not only, 'router-only post-reply job must not run code progression'
        nodes._extract_module_data = extract
        v2_workflow.record_steps = steps
        store = InMemorySessionStore(ttl_seconds=60, max_messages=40)
        await store.adopt('single', [], module, memory)
        original_set_module = store.set_module
        async def set_module(*args):
            calls['set_module'] += 1
            assert not only, 'router-only background may not overwrite the module cache'
            await original_set_module(*args)
        store.set_module = set_module
        provider = StubProvider()
        context = GraphContext(provider=provider, router_provider=provider, store=store,
            knowledge_base=StubKnowledgeBase(), settings=get_settings(), sessionmaker=maker)
        # An untrusted stale graph flag cannot authorize bypassing the DB role.
        state = {'session_id': 'single', 'subject_id': 'synthetic', 'user_input': '我最近写报告拖延',
            'final_response': '愿意说说最近一次吗？', 'extracted_intent': module, 'active_cycle_id': None,
            'routing_mode': 'router_only', 'memory': {'routing_mode': 'router_only', 'stale_snapshot': 'do-not-publish'},
            'routing_pending': True, 'chat_history': [], 'module_steps': {}}
        nodes.schedule_background_routing(state, context, assistant_message_id=2)
        await nodes.wait_for_pending_routing('single')
        assert calls == {'extract': 1, 'record_steps': 0 if only else 1, 'set_module': 0 if only else 1}, calls
        async with maker() as db:
            persisted = (await db.execute(select(rt))).mappings().one()
            assert persisted['current_module'] == module
            assert persisted['memory'] == memory
            if module == 'module_1':
                record = (await db.execute(select(metadata.tables['module_one_record']))).mappings().one()
                assert record['chief_complaint'] == '写报告拖延'
                assert record['record_status'] == 'draft'
            events = (await db.execute(select(AIExecutionEvent).where(AIExecutionEvent.stage == 'post_reply_persistence'))).scalars().all()
            assert len(events) == 1 and events[0].error_code is None
            assert events[0].event_metadata['database_write']['status'] == 'completed'
            assert (await db.get(Conversation, 1)).revision == 3
        cached = await store.get('single')
        assert cached.module == module and cached.memory == memory
        print('PASS', module, stored_mode, role)
    finally:
        await engine.dispose()
asyncio.run(main())
'''


@pytest.mark.parametrize("module,stored_mode,role", [
    *[(f"module_{n}", "router_only", "admin") for n in range(1, 5)],
    ("module_1", "router_code", "admin"),
    ("module_1", "router_only", "user"),
])
def test_background_preserves_only_router_decision_and_validates_current_role(module, stored_mode, role):
    env = {**os.environ, "DATABASE_SCHEMA_VERSION": "v2", "DATABASE_URL": "sqlite+aiosqlite:///:memory:"}
    result = subprocess.run([sys.executable, "-c", BACKGROUND, module, stored_mode, role],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
