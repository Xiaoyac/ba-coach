"""Routing experiments are created by admins and remain scoped to a chat."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import select, update

from app.models import AccountSettings, UserAccount


def test_member_cannot_create_router_only_or_supply_arbitrary_mode(client, auth_headers):
    denied = client.post('/api/conversations', headers=auth_headers, json={'routing_mode':'router_only'})
    assert denied.status_code == 403
    for body in ({'routing_mode':'other'}, {'routing_mode':'router_code','admin':True}):
        assert client.post('/api/conversations', headers=auth_headers, json=body).status_code == 422
    assert client.get('/api/conversations', headers=auth_headers).json() == []
    default = client.post('/api/conversations', headers=auth_headers)
    assert default.status_code == 201 and default.json()['routing_mode'] == 'router_only'
    explicit = client.post('/api/conversations', headers=auth_headers, json={'routing_mode':'router_code'})
    assert explicit.status_code == 201 and explicit.json()['routing_mode'] == 'router_only'


def test_unsupported_legacy_database_does_not_pretend_to_enable_experiment(client, register, db_sessionmaker):
    headers = register(username='routingadmin')
    async def promote():
        async with db_sessionmaker() as db:
            account_id = await db.scalar(select(UserAccount.id).where(UserAccount.username=='routingadmin'))
            await db.execute(update(AccountSettings).where(AccountSettings.account_id==account_id).values(role='admin'))
            await db.commit()
    asyncio.run(promote())
    response=client.post('/api/conversations',headers=headers,json={'routing_mode':'router_only'})
    assert response.status_code == 409
    assert client.get('/api/conversations',headers=headers).json() == []


def test_v2_mode_creation_reload_permissions_and_manual_transition_endpoints():
    # Runtime ORM names depend on configuration at import; use a fresh process.
    script = r'''
import asyncio
from app.config import Settings,get_settings
Settings.model_config['env_file']=None
get_settings.cache_clear()
from fastapi import FastAPI
from httpx import ASGITransport,AsyncClient
from sqlalchemy import insert,select,update,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.database_v2_schema import metadata
from app.models import AccountSettings,Conversation,ConversationMessage,ConversationReplySettings,ConversationResponseMode,ConversationReplyEffort,UserAccount
from app.db import get_db
from app.identity import require_subject_id
from app.routes import conversations,program
from app.session import InMemorySessionStore,get_session_store
from app.opening import OPENING_MESSAGE_TEXT

async def main():
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        # Include app-owned reply preferences as production Base.metadata.create_all does.
        for model in (Conversation,ConversationMessage,UserAccount,AccountSettings,ConversationReplySettings,ConversationResponseMode,ConversationReplyEffort):
            await connection.run_sync(model.__table__.create)
        from app.db import Base
        await connection.run_sync(Base.metadata.create_all)
    maker=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with maker() as db:
            for account_id,role in ((1,'admin'),(2,'user')):
                await db.execute(insert(metadata.tables['user_profile']),{'uuid':role})
                await db.execute(insert(UserAccount),{'id':account_id,'username':role,'password_hash':'unused','profile_uuid':role})
                await db.execute(insert(AccountSettings),{'account_id':account_id,'role':role})
                await db.execute(insert(metadata.tables['user_module_one_state']),{'user_id':role,'status':'in_progress','completed_steps':[]})
            await db.commit()
            actor=['admin']
            store=InMemorySessionStore(ttl_seconds=60,max_messages=40)
            app=FastAPI()
            app.include_router(conversations.router,prefix='/api')
            app.include_router(program.router,prefix='/api')
            async def test_db():
                try: yield db
                finally: await db.rollback()
            app.dependency_overrides[get_db]=test_db
            app.dependency_overrides[require_subject_id]=lambda:actor[0]
            app.dependency_overrides[get_session_store]=lambda:store
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://isolated') as client:
                pure=await client.post('/api/conversations',json={'routing_mode':'router_only'})
                assert pure.status_code==201,pure.text
                pure=pure.json()
                code=(await client.post('/api/conversations',json={'routing_mode':'router_code'})).json()
                legacy=(await client.post('/api/conversations')).json()
                assert len({pure['session_id'],code['session_id'],legacy['session_id']})==3
                for detail,mode in ((pure,'router_only'),(code,'router_code'),(legacy,'router_code')):
                    assert detail['routing_mode']==mode and detail['next_module']=='module_1'
                    assert [m['content'] for m in detail['messages']]==[OPENING_MESSAGE_TEXT]
                    runtime=metadata.tables['conversation_runtime_states']
                    row=(await db.execute(select(runtime).join(Conversation,Conversation.id==runtime.c.conversation_id)
                        .where(Conversation.session_id==detail['session_id']))).mappings().one()
                    assert row['memory']['routing_mode']==mode and row['memory']['fresh_m1'] is True
                    assert row['active_goal_id'] is None and row['active_cycle_id'] is None
                    assert (await store.get(detail['session_id'])).memory['routing_mode']==mode
                    await store.reset(detail['session_id'])
                    fetched=await client.get('/api/conversations/'+detail['session_id'])
                    assert fetched.status_code==200 and fetched.json()['routing_mode']==mode
                sid=pure['session_id']
                before=(await db.execute(select(func.count()).select_from(ConversationMessage))).scalar_one()
                selected=await client.post('/api/program/'+sid+'/goal',json={'goal_id':'unused','row_version':0})
                confirmed=await client.post('/api/program/'+sid+'/confirm',json={'record_id':'unused','record_hash':'0'*64,'row_version':0})
                assert selected.status_code==confirmed.status_code==409
                assert 'Router' in selected.text and 'Router' in confirmed.text
                assert (await db.execute(select(func.count()).select_from(ConversationMessage))).scalar_one()==before
                status=await client.get('/api/program/'+sid)
                assert status.status_code==200,status.text
                assert status.json()['routing_mode']=='router_only' and not status.json()['can_confirm']
                actor[0]='user'
                assert (await client.get('/api/conversations/'+sid)).status_code==404
                assert (await client.post('/api/conversations',json={'routing_mode':'router_only'})).status_code==403
                actor[0]='admin'
                await db.execute(update(AccountSettings).where(AccountSettings.account_id==1).values(role='user'))
                await db.commit()
                # Demoted account adopts the member router-only policy.
                after=await client.get('/api/conversations/'+sid)
                assert after.status_code==200 and after.json()['routing_mode']=='router_only'
    finally:
        await engine.dispose()
asyncio.run(main())
'''
    result=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[1],
        env={**os.environ,'DATABASE_SCHEMA_VERSION':'v2','DATABASE_URL':'sqlite+aiosqlite:///:memory:'},
        capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
