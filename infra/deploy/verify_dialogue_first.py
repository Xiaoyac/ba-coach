"""Release smoke test. Only an in-memory SQLite DB; never load production env."""
import asyncio
import json
import os

os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
os.environ['DATABASE_SCHEMA_VERSION'] = 'v2'
os.environ['STARTUP_DB_MAINTENANCE'] = 'false'
from app.config import Settings, get_settings
Settings.model_config['env_file'] = None
get_settings.cache_clear()

from sqlalchemy import insert, select, update, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database_v2_schema import metadata as schema
from app.models import Conversation, ConversationMessage
from app.v2_workflow import record_steps, runtime_for, persist_record
from app.v2_repository import create_goal, start_cycle
from app.workflow_contract import MODULE_STEP_KEYS
from app.m1_contract import VERSION
from app.answer_validator import validate_answer
from app.reply_workflow import read_reply_workflow
from app.semantic_catalog import SemanticCatalog
from app.retrieval import index_chunk
from app.knowledge_references import reference_snapshot


async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(schema.create_all)
            for model in (Conversation, ConversationMessage):
                await conn.run_sync(model.__table__.create)
        async with maker() as db:
            rt = schema.tables['conversation_runtime_states']
            await db.execute(insert(schema.tables['user_profile']), {'uuid':'synthetic-release'})
            await db.execute(insert(Conversation), {'id':1,'session_id':'synthetic-chat','subject_id':'synthetic-release'})
            await db.execute(insert(rt), {'conversation_id':1,'current_module':'module_1','memory':{}})
            consent = '我理解了，愿意开始目标设定。'
            await db.execute(insert(ConversationMessage), [
                {'id':1,'conversation_id':1,'position':1,'role':'user','content':consent},
                {'id':2,'conversation_id':1,'position':2,'role':'assistant','content':'我们慢慢讨论。'}])
            await db.execute(insert(schema.tables['module_one_record']), {'id':'synthetic-m1','user_id':'synthetic-release',
                'version_no':1,'event_experience':{'_m1_contract':{'version':VERSION,'session_id':'synthetic-chat',
                    'assistant_message_id':2,'path':'low_disclosure','missing_fields':[],
                    'completed_steps':list(MODULE_STEP_KEYS['module_1']),
                    'evidence':{'consent':{'quote':consent,'role':'user','turn':0}}}}})
            assert (await record_steps(db,session_id='synthetic-chat',user_id='synthetic-release',module='module_1',
                requested_target='module_2',steps=[],assistant_message_id=2)) == ('module_2',None)
            assert (await db.execute(select(func.count()).select_from(schema.tables['pa_goals']))).scalar_one() == 0
            goal = await create_goal(db,user_id='synthetic-release',title='散步',conversation_id=1)
            cycle = await start_cycle(db,user_id='synthetic-release',goal_id=goal,conversation_id=1)
            await db.execute(update(rt).where(rt.c.conversation_id==1).values(active_goal_id=goal,active_cycle_id=cycle))
            await db.execute(insert(ConversationMessage), [
                {'id':3,'conversation_id':1,'position':3,'role':'user','content':'请整理刚才的散步安排'},
                {'id':4,'conversation_id':1,'position':4,'role':'assistant','content':'每天晚饭后在小区散步10分钟，每天一次，下雨就室内走。你愿意尝试吗？'}])
            await db.commit()
            plan_data = {'target_activity_content':'散步','schedule_text':'晚饭后','target_activity_location':'小区',
                'target_activity_duration_minutes':10,'frequency_rule':{'schema_version':1,'text':'每天一次'},
                'potential_barriers':['下雨'],'barrier_coping_plan':[{'barrier':'下雨','plan':'室内走'}],
                '_source_session_id':'synthetic-chat','_source_assistant_message_id':4}
            await persist_record(maker,module='module_2',user_id='synthetic-release',data=plan_data,cycle_id=cycle)
            assert (await record_steps(db,session_id='synthetic-chat',user_id='synthetic-release',module='module_2',
                requested_target='module_2',steps=list(MODULE_STEP_KEYS['module_2']),assistant_message_id=4))[0]=='module_2'
            await db.execute(insert(ConversationMessage), [
                {'id':5,'conversation_id':1,'position':5,'role':'user','content':'就按这个计划试试'},
                {'id':6,'conversation_id':1,'position':6,'role':'assistant','content':'收到你的想法了。'}])
            await db.commit()
            await persist_record(maker,module='module_2',user_id='synthetic-release',cycle_id=cycle,
                data={**plan_data,'_source_assistant_message_id':6})
            assert (await record_steps(db,session_id='synthetic-chat',user_id='synthetic-release',module='module_2',
                requested_target='module_3',steps=list(MODULE_STEP_KEYS['module_2']),assistant_message_id=6))[0]=='module_3'
            agreement='每天记录活动时间、内容和活动后心情'
            await db.execute(insert(ConversationMessage), [
                {'id':7,'conversation_id':1,'position':7,'role':'user','content':'每天记录一次'},
                {'id':8,'conversation_id':1,'position':8,'role':'assistant','content':agreement+'，可以吗？'}])
            await db.commit()
            record_data={'negotiated_record_plan':agreement,'_source_session_id':'synthetic-chat','_source_assistant_message_id':8}
            await persist_record(maker,module='module_3',user_id='synthetic-release',cycle_id=cycle,data=record_data)
            await record_steps(db,session_id='synthetic-chat',user_id='synthetic-release',module='module_3',
                requested_target='module_3',steps=list(MODULE_STEP_KEYS['module_3']),assistant_message_id=8)
            await db.execute(insert(ConversationMessage), [
                {'id':9,'conversation_id':1,'position':9,'role':'user','content':'我同意这样记录'},
                {'id':10,'conversation_id':1,'position':10,'role':'assistant','content':'我们按这个方式试试。'}])
            await db.commit()
            await persist_record(maker,module='module_3',user_id='synthetic-release',cycle_id=cycle,
                data={**record_data,'_source_assistant_message_id':10})
            assert (await record_steps(db,session_id='synthetic-chat',user_id='synthetic-release',module='module_3',
                requested_target='module_4',steps=list(MODULE_STEP_KEYS['module_3']),assistant_message_id=10))[0]=='module_4'
            assert (await db.execute(select(func.count()).select_from(ConversationMessage))).scalar_one()==10
            logs=schema.tables['ai_decision_logs']
            evidence=list((await db.execute(select(logs.c.evidence_message_ids).where(
                logs.c.decision_type=='user_confirmation').order_by(logs.c.id))).scalars())
            assert evidence==[[1],[5],[9]], evidence
            await db.commit()
        authority=await read_reply_workflow(maker,'synthetic-release','synthetic-chat')
        assert authority['current_module']=='module_4' and authority['plan_confirmed']
        invalid=validate_answer(reply='请到网页的目标面板核对并完成确认。',module='module_1',evidence_ids=[],workflow=authority)
        assert invalid['status']=='blocked'
        docs=[index_chunk(id=1,source_id=1,source_name='synthetic',category='BA',heading='方法',content='先行动再观察')]
        hits=SemanticCatalog.results('{"selected_ids":["kb:1"]}',docs,top_k=1)
        snapshot=reference_snapshot(module='module_1',recalled=hits,provided=hits,retrieval={},mediator={})
        assert snapshot['recalled'][0]['score'] is None
        assert snapshot['recalled'][0]['score_type']=='model_selection'
        print(json.dumps({'ok':True,'database':'isolated in-memory SQLite','m1_to_m2_no_goal':True,
            'm2_to_m3':True,'m3_to_m4':True,'synthetic_confirmation_messages':0,
            'panel_instruction_blocked':True,'catalog_score':None}))
    finally:
        await engine.dispose()


asyncio.run(main())
