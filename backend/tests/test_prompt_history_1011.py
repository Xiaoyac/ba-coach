"""Audit and concurrency tests on real SQLite triggers, not mocked history."""
import asyncio
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.exc import DBAPIError
from fastapi import HTTPException
from app.db import Base
from app.models import PromptOverride
from app.prompt_history import initialize_history, change_prompt, snapshot, compare_text, sync_source_defaults
from app.prompt_history_models import PromptRevision, PromptHistoryState

@pytest_asyncio.fixture
async def history_db(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'prompts.db'))
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: PromptOverride.__table__.create(c))
    await initialize_history(engine)
    yield engine,async_sessionmaker(engine,expire_on_commit=False)
    await engine.dispose()

@pytest.mark.asyncio
async def test_save_restore_reset_and_immutable_history(history_db):
    engine,sessions=history_db
    async with sessions() as db:
        original=await snapshot(db,'module_2')
        saved=await change_prompt(db,'module_2',actor='管理员甲',base_revision=1,content='新的目标说明')
        assert saved.revision==2 and saved.actor=='管理员甲'
        restored=await change_prompt(db,'module_2',actor='管理员乙',base_revision=2,content=original.content,restored_from=1)
        assert restored.revision==3 and restored.restored_from==1 and restored.content==original.content
        reset=await change_prompt(db,'module_2',actor='管理员乙',base_revision=3)
        assert reset.revision==4 and reset.action=='reset_default' and not reset.is_overridden
        assert len((await db.execute(select(PromptRevision).where(PromptRevision.prompt_key=='module_2'))).scalars().all())==4
        with pytest.raises(DBAPIError):
            await db.execute(text("UPDATE prompt_revisions SET content='篡改' WHERE prompt_key='module_2'"))
        await db.rollback()
        with pytest.raises(DBAPIError):
            await db.execute(text("DELETE FROM prompt_revisions WHERE prompt_key='module_2'"))
        await db.rollback()
        assert (await snapshot(db,'module_2')).revision==4
    await initialize_history(engine)
    async with sessions() as db: assert (await snapshot(db,'module_2')).revision==4

@pytest.mark.asyncio
async def test_raw_sql_system_and_rollback_are_audited_atomically(history_db):
    _,sessions=history_db
    async with sessions() as db:
        await db.execute(text("INSERT INTO prompt_overrides(prompt_key,content,updated_by,updated_at) VALUES ('module_2','系统修改','system:deploy',CURRENT_TIMESTAMP)"))
        await db.commit()
        v=await snapshot(db,'module_2');assert (v.revision,v.actor,v.content)==(2,'system:deploy','系统修改')
        await db.execute(text("UPDATE prompt_overrides SET content='系统二次更新' WHERE prompt_key='module_2'"))
        await db.rollback()
        assert (await snapshot(db,'module_2')).revision==2
        await db.execute(text("DELETE FROM prompt_overrides WHERE prompt_key='module_2'"));await db.commit()
        v=await snapshot(db,'module_2');assert v.revision==3 and not v.is_overridden and v.actor=='system:database'

@pytest.mark.asyncio
async def test_parallel_writes_have_one_winner_and_no_lost_history(history_db):
    _,sessions=history_db
    async def write(actor):
        async with sessions() as db:
            try: return await change_prompt(db,'module_2',actor=actor,base_revision=1,content=actor)
            except HTTPException as e: return e
    results=await asyncio.gather(write('甲'),write('乙'))
    assert sum(isinstance(x,HTTPException) and x.status_code==409 for x in results)==1
    async with sessions() as db:
        v=await snapshot(db,'module_2');assert v.revision==2
        with pytest.raises(HTTPException) as e:
            await change_prompt(db,'module_2',actor='旧页面',base_revision=1,content='不该写入',restored_from=1)
        assert e.value.status_code==409
        assert (await snapshot(db,'module_2')).revision==2

@pytest.mark.asyncio
async def test_source_fallback_change_only_records_when_effective(history_db,monkeypatch):
    _,sessions=history_db
    from app import prompt_history
    class Definition:
        key='module_2';default_content='新的源码默认'
    monkeypatch.setattr(prompt_history,'PROMPT_DEFINITIONS',[Definition()])
    async with sessions() as db:
        await sync_source_defaults(db)
        v=await snapshot(db,'module_2');assert v.revision==2 and v.action=='source_update'
        await change_prompt(db,'module_2',actor='管理员',base_revision=2,content='自定义')
        Definition.default_content='另一次默认更新'
        await sync_source_defaults(db)
        assert (await snapshot(db,'module_2')).revision==3
        reset=await change_prompt(db,'module_2',actor='管理员',base_revision=3)
        assert reset.content=='另一次默认更新' and reset.revision==4

@pytest.mark.parametrize('before,after',[('原文\n不变','原文\n新增'),('我想散步','我想跑步'),('','新增'),('删除',''),('a\r\nb','a\nb'),('同一句\n'*500+'甲','同一句\n'*500+'乙'),('<script>alert(1)</script>','安全文本')])
def test_diff_reconstructs_both_versions_exactly(before,after):
    changes=compare_text(before,after)
    assert ''.join(c['text'] for c in changes if c['kind']!='insert')==before
    assert ''.join(c['text'] for c in changes if c['kind']!='delete')==after

# Exercise the actual authenticated HTTP routes and the effective model prompt.
from test_admin_prompts import admin_headers

def test_http_history_compare_restore_conflict_and_pagination(client,admin_headers,db_sessionmaker):
    headers=admin_headers
    def request(content,base):
        return client.put('/api/admin/prompts/module_2',headers=headers,json={'content':content,'base_revision':base})
    baseline=next(p for p in client.get('/api/admin/prompts',headers=headers).json()['prompts'] if p['key']=='module_2')
    assert baseline['revision']==1
    assert request('原句：每天散步。',1).status_code==200
    assert request('原句：每天跑步。\n新增一行。',2).status_code==200
    diff=client.get('/api/admin/prompts/module_2/compare?from_revision=2&to_revision=3',headers=headers).json()
    assert ''.join(c['text'] for c in diff['changes'] if c['kind']!='insert')=='原句：每天散步。'
    assert ''.join(c['text'] for c in diff['changes'] if c['kind']!='delete')=='原句：每天跑步。\n新增一行。'
    assert client.get('/api/admin/prompts/module_1/history/3',headers=headers).status_code==404
    assert client.post('/api/admin/prompts/module_2/history/2/restore',headers=headers,json={'base_revision':2}).status_code==409
    restored=client.post('/api/admin/prompts/module_2/history/2/restore',headers=headers,json={'base_revision':3})
    assert restored.status_code==200 and restored.json()['revision']==4
    assert restored.json()['content']=='原句：每天散步。'
    async def effective():
        from app.prompt_store import effective_prompt_pair
        async with db_sessionmaker() as db:return await effective_prompt_pair(db,'module_2')
    assert asyncio.run(effective())[1]=='原句：每天散步。'
    assert request('旧窗口不能覆盖',3).status_code==409
    assert client.delete('/api/admin/prompts/module_2?base_revision=3',headers=headers).status_code==409
    page=client.get('/api/admin/prompts/module_2/history?limit=2',headers=headers).json()
    assert [v['revision'] for v in page['items']]==[4,3] and page['next_before']==3
    assert page['items'][0]['restored_from']==2 and page['items'][0]['actor']=='promptadmin'  # unique login, not a nickname
    second=client.get('/api/admin/prompts/module_2/history?limit=2&before=3',headers=headers).json()
    assert [v['revision'] for v in second['items']]==[2,1] and second['next_before'] is None
    assert client.delete('/api/admin/prompts/module_2?base_revision=4',headers=headers).json()['revision']==5
    assert client.get('/api/admin/prompts/module_2/history/3',headers=headers).json()['content']=='原句：每天跑步。\n新增一行。'


def test_history_is_admin_only_and_old_clients_cannot_overwrite(client,auth_headers,admin_headers):
    for path in ['/api/admin/prompts/module_2/history','/api/admin/prompts/module_2/history/1','/api/admin/prompts/module_2/compare?from_revision=1&to_revision=1']:
        assert client.get(path,headers=auth_headers).status_code==403
        assert client.get(path).status_code==401
    assert client.post('/api/admin/prompts/module_2/history/1/restore',headers=auth_headers,json={'base_revision':1}).status_code==403
    assert client.put('/api/admin/prompts/module_2',headers=admin_headers,json={'content':'无版本号'}).status_code==422
    assert client.delete('/api/admin/prompts/module_2',headers=admin_headers).status_code==422

@pytest.mark.asyncio
async def test_initial_migration_retains_original_author_time_and_content(tmp_path):
    from datetime import datetime,timezone
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'migration.db'))
    async with engine.begin() as conn: await conn.run_sync(lambda c:PromptOverride.__table__.create(c))
    maker=async_sessionmaker(engine,expire_on_commit=False)
    stamp=datetime(2026,10,10,7,26,34,tzinfo=timezone.utc)
    async with maker() as db:
        db.add(PromptOverride(prompt_key='module_2',content='管理员原始版本\n',updated_by='原管理员',updated_at=stamp));await db.commit()
    await initialize_history(engine)
    async with maker() as db:
        v=await snapshot(db,'module_2')
        assert v.action=='baseline' and v.actor=='原管理员' and v.created_at==stamp and v.content=='管理员原始版本\n'
        row=(await db.execute(select(PromptOverride))).scalar_one()
        assert row.content==v.content and row.updated_at==stamp
    await engine.dispose()
