"""Real-model API acceptance with synthetic data and an isolated SQLite DB.

Run on the host with --provider-config; copy only provider settings, never DB
or authentication secrets. No production DB access and no service switching.
"""
import argparse
import os
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('--provider-config',required=True)
parser.add_argument('--serve', action='store_true')
args=parser.parse_args()
from dotenv import dotenv_values
for key,value in dotenv_values(args.provider_config).items():
    if key.startswith(('DEEPSEEK_','DOUBAO_')) and value:
        os.environ[key]=value
os.environ['DEFAULT_PROVIDER']='deepseek'
import smoke_v2_application as b
import asyncio,json,time
from sqlalchemy import select,update
from app.routes import chat
from app import db as db_module
from app import retrieval
from app.graph import nodes
from app.retrieval import StubKnowledgeBase

async def main():
    engine=b.create_async_engine('sqlite+aiosqlite://')
    async with engine.begin() as c:
        await c.exec_driver_sql('PRAGMA foreign_keys=ON')
        await c.run_sync(b.schema.create_all)
        await c.run_sync(lambda conn:b.Base.metadata.create_all(conn,tables=[t for t in b.Base.metadata.sorted_tables if t.name not in b.schema.tables]))
        await c.run_sync(b.RiskMonitoring.__table__.create)
        await c.run_sync(b.InteractionStatus.__table__.create)
    maker=b.async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    db_module._sessionmaker=maker
    db_module._engine=engine
    async def dep():
        async with maker() as db: yield db
    b.app.dependency_overrides[b.get_db]=dep
    b.auth.email_delivery_configured=lambda:False
    # Local deterministic search over synthetic text; real mediator and module.
    retrieval._knowledge_base=StubKnowledgeBase({'module_2':[
        ('synthetic-m2','活动目标 散步 阅读 时间 地点 时长 频率 障碍 应对。活动是用户自主选择的具体行动，不是必须运动。身体不适时先澄清，不把知识当诊断，不鼓励忍痛执行。') ]})
    results=[]
    async with b.httpx.AsyncClient(transport=b.httpx.ASGITransport(app=b.app),base_url='http://isolated',timeout=180) as client:
        async def req(method,path,data=None):
            r=await client.request(method,'/api'+path,json=data)
            assert r.status_code<400,(path,r.status_code,r.text[:300])
            return r.json() if r.content else None
        a=await req('POST','/auth/register',dict(username='realcontracttest',password='SyntheticTestOnly123',email='e2e@example.com',nickname='验收',tag='78234',age=28))
        client.headers['Authorization']='Bearer '+(a.get('token') or a['access_token'])
        uid=(await req('GET','/auth/me'))['profile_uuid']
        async with maker() as db:
            m=b.schema.tables['user_module_one_state']
            await db.execute(update(m).where(m.c.user_id==uid).values(status='completed',completion_source='legacy_imported',evidence_status='missing'))
            await db.commit()
        sid=(await req('POST','/conversations'))['session_id']
        p=await req('GET',f'/program/{sid}')
        await req('POST',f'/program/{sid}/goal',{'title':'晚饭后选一个可行的小活动','row_version':p['runtime']['row_version']})
        inputs=[
            '我想每天晚饭后散步，但最近膝盖疼，地点和时长还没决定。可以先别确定计划吗？',
            '我理解PA是具体的行动，不一定是运动。为了放松和保持学习，我自主选择改为在家沙发上读小说。每天晚饭后读10分钟，独自进行。可能的障碍是忘记，我会把书放餐桌旁提醒自己。这个安排我愿意尝试。请整理目标卡。',
            '我确认：活动是在家沙发上读小说，每天晚饭后10分钟，独自完成；忘记时靠餐桌旁的书提醒。我理解行动任务和BA方法的区别，也确认这个目标对我有放松和学习的意义，可以进入下一步。',
        ]
        for index,message in enumerate(inputs):
            started=time.monotonic()
            body={'session_id':sid,'message':message,'provider':'deepseek'}
            if index == 2:
                response=await asyncio.wait_for(client.post('/api/chat/stream',json=body),150)
                assert response.status_code==200
                events=[]
                for frame in response.text.replace('\r\n','\n').split('\n\n'):
                    lines=frame.splitlines()
                    kind=next((s[7:] for s in lines if s.startswith('event: ')),None)
                    data=next((json.loads(s[6:]) for s in lines if s.startswith('data: ')),None)
                    if kind: events.append((kind,data))
                assert not any(k=='error' for k,d in events),events
                assert any(k=='persisted' and d.get('saved') for k,d in events)
                reply={'reply':''.join(d.get('text','') for k,d in events if k=='delta')}
            else:
                reply=await asyncio.wait_for(req('POST','/chat',body),150)
            await asyncio.wait_for(nodes.wait_for_pending_routing(sid),100)
            while nodes._background_tasks:
                await asyncio.wait_for(asyncio.gather(*list(nodes._background_tasks),return_exceptions=True),100)
            p=await req('GET',f'/program/{sid}')
            snapshot=await req('GET',f'/conversations/{sid}')
            saved=any(x.get('content')==reply['reply'] for x in snapshot.get('messages',[]) if x.get('role')=='assistant')
            row=dict(input=message,reply=reply['reply'],reply_saved=saved,seconds=round(time.monotonic()-started,2),
                module=p['runtime']['current_module'],can_confirm=p['can_confirm'],missing_fields=p.get('missing_fields'),draft_present=bool(p['draft']))
            results.append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
            assert reply['reply'] and saved,'Reply must persist and survive refresh'
        assert p['can_confirm'],'BLOCKER: complete user plan never became confirmable'
        assert not p.get('missing_fields'),'BLOCKER: extracted plan incomplete'
        p=await req('POST',f'/program/{sid}/confirm',{'record_id':p['draft']['id'],'record_hash':p['record_hash'],'row_version':p['runtime']['row_version']})
        assert p['runtime']['current_module']=='module_3'
        # API success is not semantic acceptance. Explicitly flag the exact
        # premature-state claims observed in the first real-model run.
        premature = [i+1 for i,row in enumerate(results) if row['module']=='module_2' and
            any(s in row['reply'] for s in ['目标卡片已锁定','接下来进入模块三','已经进入模块三'])]
        print(json.dumps({'api_flow_passed':True,'acceptance_passed':not premature,
            'premature_state_claim_turns':premature,'production_accessed':False,
            'real_provider':'deepseek','turns':len(results)}),flush=True)
        assert not premature,'BLOCKER: reply claims a committed transition before database confirmation'
        if args.serve:
            @b.app.get('/api/acceptance/bootstrap')
            async def bootstrap():
                return {'token':a.get('token') or a['access_token'],'session_id':sid}
            import uvicorn
            print('ISOLATED_BROWSER_BACKEND_READY',flush=True)
            await uvicorn.Server(uvicorn.Config(b.app,host='127.0.0.1',port=8128,lifespan='off')).serve()
    await engine.dispose()

if __name__=='__main__':
    asyncio.run(main())
