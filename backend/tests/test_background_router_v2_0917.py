"""Background facts preserve the one pre-reply Router decision and trace."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


# A fresh interpreter is required: ORM column names are chosen at import time.
# Never inherit the developer's database configuration or call a real provider.
SCENARIO = r'''
import asyncio, json, sys
from app.config import Settings, get_settings
Settings.model_config['env_file'] = None
get_settings.cache_clear()
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database_v2_schema import metadata
from app.models import Conversation, ConversationMessage, AIExecutionEvent
from app.models_business import InteractionStatus
from app.graph.nodes import schedule_background_routing, wait_for_pending_routing
from app.graph.state import GraphContext
from app.retrieval import StubKnowledgeBase
from app.session import InMemorySessionStore
sys.path.insert(0, 'tests')
from conftest import StubProvider

async def main():
    module = sys.argv[1]
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            for model in (Conversation, ConversationMessage, AIExecutionEvent, InteractionStatus):
                await conn.run_sync(model.__table__.create)
        runtime = metadata.tables['conversation_runtime_states']
        async with maker() as db:
            await db.execute(insert(metadata.tables['user_profile']), {'uuid':'synthetic'})
            await db.execute(insert(Conversation), {'id':1, 'session_id':'synthetic-chat', 'subject_id':'synthetic', 'revision':2})
            await db.execute(insert(ConversationMessage), [
                {'id':1,'conversation_id':1,'position':0,'role':'user','content':'你好'},
                {'id':2,'conversation_id':1,'position':1,'role':'assistant','content':'你好，我们慢慢聊。','routing_reasoning_content':'前置已提交决定','router_model_name':'pre-router'}])
            await db.execute(insert(runtime), {'conversation_id':1,'current_module':module,'memory':{'freshness_marker':'preserve'}})
            await db.commit()
        provider = StubProvider()
        provider.route_result = json.dumps({'target_module':module,'completed_steps':[]})
        store = InMemorySessionStore(ttl_seconds=60,max_messages=40)
        await store.adopt('synthetic-chat', [], module, {})
        context = GraphContext(provider=provider,router_provider=provider,store=store,
            knowledge_base=StubKnowledgeBase(),settings=get_settings(),sessionmaker=maker)
        state = {'session_id':'synthetic-chat','subject_id':'synthetic','user_input':'你好',
            'final_response':'你好，我们慢慢聊。','extracted_intent':module,'active_cycle_id':None,
            'memory':{'stale_snapshot':'must not overwrite'},'chat_history':[],
            'module_steps':{},'routing_pending':True,'error':None}
        schedule_background_routing(state,context,assistant_message_id=2)
        await wait_for_pending_routing('synthetic-chat')
        async with maker() as db:
            row = await db.get(ConversationMessage,2)
            assert row.routing_reasoning_content == '前置已提交决定', 'Background must preserve the pre-reply trace'
            assert row.router_model_name == 'pre-router'
            assert row.content == '你好，我们慢慢聊。'
            conv = await db.get(Conversation,1)
            assert conv.revision == 3, 'Live clients need a new revision'
            saved = (await db.execute(select(runtime).where(runtime.c.conversation_id==1))).mappings().one()
            assert saved['current_module'] == module
            assert saved['memory']['freshness_marker'] == 'preserve'
            assert 'stale_snapshot' not in saved['memory']
            events = (await db.execute(select(AIExecutionEvent).where(AIExecutionEvent.stage=='module_router'))).scalars().all()
            assert events == [], 'No second Router call after display'
            writes = (await db.execute(select(AIExecutionEvent).where(AIExecutionEvent.stage=='post_reply_persistence'))).scalars().all()
            assert len(writes) == 1
            assert writes[0].provider is None
            assert writes[0].event_metadata['execution_timeline'][0]['after_display'] is True
        assert (await store.get('synthetic-chat')).module == module
        assert not any('target_module' in system for system in provider.route_systems)
        print('PASS', module, 'pre-reply trace preserved, facts refreshed')
    finally:
        await engine.dispose()
asyncio.run(main())
'''


@pytest.mark.parametrize('module', ['module_1', 'module_2', 'module_3', 'module_4'])
def test_v2_background_facts_preserve_pre_reply_router(module):
    env = {**os.environ, 'DATABASE_SCHEMA_VERSION':'v2', 'DATABASE_URL':'sqlite+aiosqlite:///:memory:'}
    result = subprocess.run([sys.executable, '-c', SCENARIO, module], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'background router failed' not in result.stderr
