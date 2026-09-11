"""Offline contract probes. Synthetic data; no real LLM or production access."""
import smoke_v2_application as base
import asyncio
import json
from types import SimpleNamespace
from sqlalchemy import select, update
from app.v2_workflow import clinical_context
from app.knowledge_mediator import mediate_knowledge
from app.retrieval import KnowledgeChunk


async def main():
    engine = base.create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as c:
        await c.exec_driver_sql("PRAGMA foreign_keys=ON")
        await c.run_sync(base.schema.create_all)
        await c.run_sync(lambda conn: base.Base.metadata.create_all(conn, tables=[t for t in base.Base.metadata.sorted_tables if t.name not in base.schema.tables]))
        await c.run_sync(base.RiskMonitoring.__table__.create)
        await c.run_sync(base.InteractionStatus.__table__.create)
    maker = base.async_sessionmaker(engine, expire_on_commit=False)
    async def dependency():
        async with maker() as db:
            yield db
    base.app.dependency_overrides[base.get_db] = dependency
    base.auth.email_delivery_configured = lambda: False
    results = []
    def check(name, ok, observed):
        results.append(dict(name=name, contract_pass=bool(ok), observed=observed))
    async with base.httpx.AsyncClient(transport=base.httpx.ASGITransport(app=base.app), base_url="http://test") as client:
        async def req(method, path, body=None):
            r = await client.request(method, '/api'+path, json=body)
            assert r.status_code < 400, (path, r.status_code, r.text[:200])
            return r.json() if r.content else None
        account = await req('POST','/auth/register',dict(username='contractaudit',password='OfflineOnly12345',email='audit@example.com',nickname='隔离测试',tag='87654',age=28))
        client.headers['Authorization'] = 'Bearer '+(account.get('token') or account['access_token'])
        user = (await req('GET','/auth/me'))['profile_uuid']
        async with maker() as db:
            t = base.schema.tables['user_module_one_state']
            await db.execute(update(t).where(t.c.user_id==user).values(status='completed',completion_source='legacy_imported',evidence_status='missing'))
            await db.commit()
        async def goal(title):
            chat = await req('POST','/conversations')
            sid=chat['session_id']
            p=await req('GET',f'/program/{sid}')
            p=await req('POST',f'/program/{sid}/goal',dict(title=title,row_version=p['runtime']['row_version']))
            return sid,p
        sid,p = await goal('晚饭后散步')
        cycle=p['runtime']['active_cycle_id']
        from app.knowledge_context import assemble_knowledge_context
        await req('PATCH','/profile',{'physical_condition':['慢性疼痛']})
        bundle=await assemble_knowledge_context(maker,user,sid)
        check('structured_context_preserves_constraint_source',any(f['source']=='user_activity_constraints' and f['source_kind']=='profile_form' and '慢性疼痛' in f['values']['content'] for f in bundle['facts']),bundle)
        other,op=await goal('阅读小说')
        ctx=await clinical_context(maker,user,other)
        check('different_goal_context_isolation','晚饭后散步' not in '\n'.join(ctx),ctx)
        async with maker() as db:
            convo=(await db.execute(select(base.Conversation.id).where(base.Conversation.session_id==sid))).scalar_one()
            m=base.ConversationMessage(conversation_id=convo,position=2,role='user',content='我想每天晚饭后散步，但最近膝盖疼，还没决定地点和时长。')
            db.add(m)
            await db.commit()
            evidence=m.id
        await base.persist_record(maker,module='module_2',user_id=user,cycle_id=cycle,data={'target_activity_content':'散步','schedule_text':'每天晚饭后'})
        p=await req('GET',f'/program/{sid}')
        check('draft_does_not_automatically_complete',not p['can_confirm'],p['can_confirm'])
        # Inject an erroneous router result to test the deterministic backstop,
        # not to claim the actual model produces this result.
        async with maker() as db:
            await base.record_steps(db,session_id=sid,user_id=user,module='module_2',requested_target='module_3',steps=list(base.MODULE_STEP_KEYS['module_2']),assistant_message_id=evidence)
            await db.commit()
        p=await req('GET',f'/program/{sid}')
        old=dict(record_id=p['draft']['id'],record_hash=p['record_hash'],row_version=p['runtime']['row_version'])
        await base.persist_record(maker,module='module_2',user_id=user,cycle_id=cycle,data={'target_activity_content':'暂未决定，用户纠正膝盖仍疼'})
        r=await client.post(f'/api/program/{sid}/confirm',json=old)
        check('stale_draft_rejected',r.status_code==409,r.status_code)
        # User correction should invalidate prior readiness, not only its hash.
        async with maker() as db:
            await base.record_steps(db,session_id=sid,user_id=user,module='module_2',requested_target='module_2',steps=[],assistant_message_id=evidence)
            await db.commit()
        p=await req('GET',f'/program/{sid}')
        check('correction_revokes_completion_readiness',not p['can_confirm'],p['can_confirm'])
        async with maker() as db:
            await base.record_steps(db,session_id=sid,user_id=user,module='module_2',requested_target='module_3',steps=list(base.MODULE_STEP_KEYS['module_2']),assistant_message_id=evidence)
            await db.commit()
        p=await req('GET',f'/program/{sid}')
        body=dict(record_id=p['draft']['id'],record_hash=p['record_hash'],row_version=p['runtime']['row_version'])
        r=await client.post(f'/api/program/{sid}/confirm',json=body)
        check('incomplete_plan_blocked_after_bad_router',r.status_code==409,r.status_code)
        await base.persist_record(maker,module='module_2',user_id=user,cycle_id=cycle,data={
            'activity_content':'阅读小说','schedule_text':'晚饭后','location':'家里','duration_minutes':10,
            'frequency_rule':{'schema_version':1,'text':'每天'},'potential_barriers':['忘记'],
            'barrier_coping_plan':[{'barrier':'忘记','plan':'把书放桌上'}]})
        async with maker() as db:
            await base.record_steps(db,session_id=sid,user_id=user,module='module_2',requested_target='module_3',steps=list(base.MODULE_STEP_KEYS['module_2']),assistant_message_id=evidence)
            await db.commit()
        p=await req('GET',f'/program/{sid}')
        body=dict(record_id=p['draft']['id'],record_hash=p['record_hash'],row_version=p['runtime']['row_version'])
        r=await client.post(f'/api/program/{sid}/confirm',json=body)
        check('complete_plan_can_confirm',r.status_code==200,r.status_code)
        r2=await client.post(f'/api/program/{sid}/confirm',json=body)
        check('duplicate_confirmation_rejected',r2.status_code==409,r2.status_code)
        p2=await req('GET',f'/program/{other}')
        check('other_goal_not_advanced',p2['runtime']['current_module']=='module_2',p2['runtime']['current_module'])
    chunks=[KnowledgeChunk(id='walk',text='示例知识：散步是一种候选活动，不代表每个人都适合。',source='synthetic')]
    captured={}
    class Provider:
        async def route_detailed(self,**kw):
            captured.update(json.loads(kw['user']))
            return SimpleNamespace(text='{"selected_ids":[],"guidance":"身体情况待澄清，不采用活动建议。"}',model='fake',usage={})
    settings=SimpleNamespace(knowledge_mediator_enabled=True,knowledge_mediator_timeout_seconds=1)
    state=dict(user_input='我最近膝盖疼，之前的散步计划先不要执行。',clinical_context=['长期记忆（unconfirmed / ai_inference）：用户喜欢散步'])
    selected,block,metrics=await mediate_knowledge(state=state,module='module_2',knowledge=chunks,provider=Provider(),settings=settings)
    check('latest_correction_delivered',captured['user_input']==state['user_input'],captured['user_input'])
    check('explicit_inapplicable_chunks_removed',selected==[],metrics['reason'])
    check('task_and_provenance_structured',all(k in captured for k in ['task','goal_id','cycle_id','facts']) and 'confirmed_context' not in captured and captured['unverified_context']==state['clinical_context'],list(captured))
    class Timeout:
        async def route_detailed(self,**kw):
            raise TimeoutError
    selected,block,metrics=await mediate_knowledge(state=state,module='module_2',knowledge=chunks,provider=Timeout(),settings=settings)
    check('timeout_withholds_unvalidated_knowledge',not selected,dict(count=len(selected),reason=metrics['reason']))
    selected,block,metrics=await mediate_knowledge(state=state,module='module_2',knowledge=[],provider=Timeout(),settings=settings)
    check('empty_retrieval_skips_mediator',metrics['reason']=='no_knowledge',metrics)
    await engine.dispose()
    print(json.dumps(dict(results=results,passed=sum(r['contract_pass'] for r in results),total=len(results),production_accessed=False,real_llm_called=False),ensure_ascii=False,indent=2))
    if not all(r['contract_pass'] for r in results):
        raise SystemExit(1)

if __name__=='__main__':
    asyncio.run(main())
