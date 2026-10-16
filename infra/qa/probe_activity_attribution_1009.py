"""Synthetic real-provider extraction against isolated SQLite only.

Run with backend on PYTHONPATH; the caller passes an initialized low-effort
provider. This module never creates a production DB engine or user message.
"""
import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.database_v2_schema import metadata as schema
from app.db import Base
from app.models import Conversation, ConversationMessage
from app.clinical_extraction import extract_module_record_detailed
from app.activity_attribution import activity_candidates, ATTRIBUTION_SCHEMA
from app.goal_contract import capture_activities, evidence_messages
from app.v2_workflow import runtime_for


async def seed(db, *, current_name='站桩'):
    goals, plans, cycles, rt = (schema.tables[n] for n in ('pa_goals', 'module_two_record', 'pa_cycles', 'conversation_runtime_states'))
    await db.execute(insert(ConversationMessage), [
        {'id':1,'conversation_id':1,'position':1,'role':'user','content':'确认旧站桩计划', 'created_at':datetime(2026,10,1)},
        {'id':2,'conversation_id':1,'position':2,'role':'user','content':'确认新的'+current_name+'计划', 'created_at':datetime(2026,10,9)}])
    await db.execute(update(goals).where(goals.c.id == 'g1').values(title='站桩', status='replaced', created_at=datetime(2026,10,1)))
    await db.execute(update(goals).where(goals.c.id == 'g2').values(title=current_name, status='active', created_at=datetime(2026,10,9)))
    for gid, pid, stamp, name in [('g1','old-plan',datetime(2026,10,1),'站桩'), ('g2','new-plan',datetime(2026,10,9), current_name)]:
        await db.execute(insert(plans), {'id':pid,'goal_id':gid,'version_no':1,'timezone':'Asia/Shanghai',
            'record_status':'confirmed','confirmation_status':'confirmed','confirmation_message_id':1 if gid=='g1' else 2,
            'activity_content':name,'schedule_text':'每晚一次','created_at':stamp})
        await db.execute(update(goals).where(goals.c.id == gid).values(current_plan_record_id=pid))
    await db.execute(insert(cycles), [
        {'id':'old-cycle','goal_id':'g1','ordinal':1,'module_two_record_id':'old-plan','status':'completed',
         'created_at':datetime(2026,10,1),'completed_at':datetime(2026,10,8,15)},
        {'id':'new-cycle','goal_id':'g2','ordinal':1,'module_two_record_id':'new-plan','status':'waiting_execution',
         'created_at':datetime(2026,10,9),'completed_at':None}])
    await db.execute(insert(schema.tables['pa_cycle_progress']), {'cycle_id':'new-cycle','module_2_steps':[], 'module_3_steps':[], 'module_4_steps':[]})
    await db.execute(update(rt).where(rt.c.conversation_id == 1).values(current_module='module_4',active_goal_id='g2',
        active_cycle_id='new-cycle',flow_status='waiting_execution'))
    await db.commit()


async def message(db, text, *, mid=100, day=9, hour=8):
    await db.execute(insert(ConversationMessage), {'id':mid,'conversation_id':1,'position':mid,'role':'user',
        'content':text, 'created_at':datetime(2026,10,day,hour)})
    await db.commit()
    return await db.get(ConversationMessage, mid)


async def run_cases(provider, *, selected=None):
    cases = [
        ('old_same_name','站桩','昨天完成了上周的旧站桩目标。我说的是旧计划，不是今天刚制定的新站桩安排。', 'g1'),
        ('current_again','跑步','今天按我们刚确定的跑步计划跑完了。晚上又跑了一次，这是第二次。', 'g2'),
        ('mixed','跑步','今天计划的跑步没做，但另外临时跳绳了十分钟。', 'g2'),
        ('ambiguous','站桩','站桩做完了，但我记不清是我们之前哪次安排了。', None),
        ('same_name_current','站桩','不是原来的那次，是按我们刚定的新站桩安排做完了。', 'g2'),
        ('recent_current','跑步','刚才按当前跑步计划做完了。', 'g2'),
    ]
    reports=[]
    for key,title,body,expected_goal in cases:
        if selected and key not in selected:
            continue
        engine=create_async_engine('sqlite+aiosqlite://')
        try:
            async with engine.begin() as conn:
                await conn.run_sync(schema.create_all)
                await conn.run_sync(Base.metadata.create_all)
            maker=async_sessionmaker(engine,expire_on_commit=False)
            async with maker() as db:
                await db.execute(insert(schema.tables['user_profile']), {'uuid':'a'})
                await db.execute(insert(Conversation),{'id':1,'session_id':'chat-a','subject_id':'a'})
                await db.execute(insert(schema.tables['conversation_runtime_states']), {'conversation_id':1,'memory':{}})
                await db.execute(insert(schema.tables['pa_goals']), [
                    {'id':'g1','user_id':'a','title':'站桩','status':'active'},
                    {'id':'g2','user_id':'a','title':title,'status':'active'}])
                await seed(db,current_name=title)
                msg=await message(db,body)
                candidates=await activity_candidates(db,'a',conversation_id=1,source_text=body)
                transcript='只读当前执行绑定：goal_id=g2,cycle_id=new-cycle,plan_id=new-plan。\n'
                transcript+='服务器消息索引：'+json.dumps([{'message_id':m.id,'role':m.role,'content':m.content,
                    'created_at':str(m.created_at)+' UTC'} for m in await evidence_messages(db,1,'a')],ensure_ascii=False)
                transcript+='\nactivity_candidates='+json.dumps(candidates,ensure_ascii=False)+'\n'+ATTRIBUTION_SCHEMA
                raw,completion=await extract_module_record_detailed(provider,module='module_4',transcript=transcript,max_tokens=6000)
                conv,state=await runtime_for(db,'chat-a')
                result=await capture_activities(db,user_id='a',conversation=conv,state=state,
                    raw=raw.get('activity_observations',[]),messages=await evidence_messages(db,1,'a'))
                await db.commit()
                rows=(await db.execute(select(schema.tables['pa_activity_events']))).mappings().all()
                report={'case':key,'model':completion.model,'request_id':completion.request_id,
                    'extraction_error':completion.extraction_error_code,'observations':raw.get('activity_observations'),
                    'attributions':result,'saved':[{'activity':r['activity_content'],'kind':r['event_kind'],
                        'goal_id':r['goal_id'],'cycle_id':r['cycle_id']} for r in rows]}
                if key=='old_same_name':
                    report['passed']=bool(rows) and all(r['goal_id']=='g1' for r in rows)
                elif key=='ambiguous':
                    report['passed']=bool(result) and all(r['attribution_status']=='uncertain' for r in result)
                elif key=='mixed':
                    report['passed']=any(r['activity_content']=='跑步' and r['event_kind']=='not_performed' and r['goal_id']=='g2' for r in rows) and any('跳绳' in r['activity_content'] and r['goal_id'] is None for r in rows)
                elif key=='current_again':
                    report['passed']=len({r['execution_id'] for r in result or [] if r.get('execution_id')})==2
                else:
                    report['passed']=bool(rows) and all(r['goal_id']==expected_goal for r in rows)
                reports.append(report)
                print(json.dumps(report,ensure_ascii=False,default=str),flush=True)
        finally:
            await engine.dispose()
    return reports
