"""Isolated database regressions: no production data, panels, or provider calls."""
import pytest
from sqlalchemy import insert, select, update, func
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.v2_workflow import record_steps, runtime_for, persist_record
from app.workflow_contract import MODULE_STEP_KEYS
from app.dialogue_confirmation import affirmative, summary_present
from app.answer_validator import validate_answer
from test_goal_overview import goal_api
from test_m1_program_0914 import seed as seed_m1
from test_extraction_freshness_0916 import seed_m2
from test_cycle_program_0914 import seed_review


@pytest.mark.parametrize('text', ['好的', '我愿意按这个计划试试', '好的，我觉得这个安排挺合适的，就按这个来'])
def test_scoped_consent_language(text):
    assert affirmative(text)


@pytest.mark.parametrize('text', ['不同意', '好的，但是改成20分钟', '我愿意吗？', '他说“我同意”', '先等等', '我同意，不过下雨就不做了'])
def test_corrections_questions_and_quotes_are_not_consent(text):
    assert not affirmative(text)


@pytest.mark.parametrize('text', [
    '麻烦你到网页的目标面板核对一下相关记录并完成确认，网页确认成功后我们就可以进入目标设定环节了。',
    '你误会啦，现在还没定具体目标。让你去目标面板是先核对我们前面聊的记录，确认无误后才开始。',
    '等你觉得合适了，再去面板确认保存。',
])
def test_screenshot_instructions_are_blocked(text):
    result = validate_answer(reply=text, module='module_1', evidence_ids=[], workflow={'available':True,'current_module':'module_1'})
    assert any(x['code'] == 'panel_confirmation_instruction' for x in result['findings'])


def test_read_only_panel_explanation_is_allowed():
    assert validate_answer(reply='目标面板仅供回顾，不需要去那里确认，直接在聊天里讨论。',
        module='module_2', evidence_ids=[], workflow={})['status'] == 'passed'


@pytest.mark.asyncio
@pytest.mark.parametrize('consent,expected', [('我理解了，愿意开始目标设定。','module_2'), ('好的','module_1'), ('我不愿意开始目标设定。','module_1')])
async def test_m1_dialogue_consent_does_not_create_goal(goal_api, consent, expected):
    _, db, _ = goal_api
    await seed_m1(db)
    await db.execute(insert(ConversationMessage), {'id':19,'conversation_id':1,'position':0,'role':'user','content':consent})
    table = schema.tables['module_one_record']
    event = (await db.execute(select(table.c.event_experience).where(table.c.id=='m1-draft'))).scalar_one()
    event['_m1_contract']['evidence'] = {'consent': {'role':'user','quote':consent,'turn':0}}
    await db.execute(update(table).where(table.c.id=='m1-draft').values(event_experience=event))
    before = (await db.execute(select(func.count()).select_from(schema.tables['pa_goals']))).scalar_one()
    target, cycle = await record_steps(db, session_id='chat-a',user_id='a',module='module_1',
        requested_target='module_2',steps=[],assistant_message_id=20)
    assert target == expected and cycle is None
    assert (await db.execute(select(func.count()).select_from(schema.tables['pa_goals']))).scalar_one() == before
    assert (await db.execute(select(func.count()).select_from(ConversationMessage))).scalar_one() == 2
    if expected == 'module_2':
        row = (await db.execute(select(table).where(table.c.id=='m1-draft'))).mappings().one()
        assert row['confirmation_message_id'] == 19
        assert row['record_status'] == 'confirmed'


SUMMARY = '计划是晚饭后散步十分钟，每天晚饭后，在小区，每次10分钟，每天；遇到下雨就室内走。你愿意按这个计划试试吗？'


async def stage_m2(db):
    await seed_m2(db)
    await db.execute(insert(ConversationMessage), {'id':19,'conversation_id':1,'position':0,'role':'user','content':'我们把计划整理一下'})
    await db.execute(update(ConversationMessage).where(ConversationMessage.id==20).values(content=SUMMARY))
    await record_steps(db,session_id='chat-a',user_id='a',module='module_2',requested_target='module_2',steps=[],assistant_message_id=20)


async def consent_turn(db, text='就按这个计划试试', *, fresh=True):
    await db.execute(insert(ConversationMessage), [
        {'id':21,'conversation_id':1,'position':2,'role':'user','content':text},
        {'id':22,'conversation_id':1,'position':3,'role':'assistant','content':'收到你的想法了。'}])
    if fresh:
        _, state = await runtime_for(db,'chat-a')
        memory = dict(state['memory'])
        memory['module_extraction_freshness'] = {'module_2':{'assistant_message_id':22,'cycle_id':'m2-cycle'}}
        rt = schema.tables['conversation_runtime_states']
        await db.execute(update(rt).where(rt.c.conversation_id==1).values(memory=memory))


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['success','correction','stale','changed_draft','other_user','late_router'])
async def test_m2_requires_same_plan_current_evidence_and_real_consent(goal_api,case):
    _,db,_ = goal_api
    await stage_m2(db)
    await consent_turn(db,'好的，但是改成20分钟' if case=='correction' else '就按这个计划试试', fresh=case!='stale')
    if case=='changed_draft':
        t=schema.tables['module_two_record']
        await db.execute(update(t).where(t.c.id=='m2-draft').values(duration_minutes=20))
    if case=='other_user':
        with pytest.raises(ValueError):
            await record_steps(db,session_id='chat-a',user_id='b',module='module_2',requested_target='module_3',steps=list(MODULE_STEP_KEYS['module_2']),assistant_message_id=22)
        return
    target,_=await record_steps(db,session_id='chat-a',user_id='a',module='module_2',requested_target='module_3',steps=list(MODULE_STEP_KEYS['module_2']),assistant_message_id=20 if case=='late_router' else 22)
    assert target == ('module_3' if case=='success' else 'module_2')
    row=(await db.execute(select(schema.tables['module_two_record']).where(schema.tables['module_two_record'].c.id=='m2-draft'))).mappings().one()
    assert row['record_status']==('confirmed' if case=='success' else 'draft')
    if case=='success':
        assert row['confirmation_message_id']==21
        # Duplicate router cannot reconfirm the same user statement.
        assert (await record_steps(db,session_id='chat-a',user_id='a',module='module_2',requested_target='module_3',steps=[],assistant_message_id=22))[0]=='module_3'
        logs=schema.tables['ai_decision_logs']
        assert (await db.execute(select(func.count()).select_from(logs).where(logs.c.decision_type=='user_confirmation'))).scalar_one()==1


@pytest.mark.asyncio
@pytest.mark.parametrize('decision,expected', [(1,'module_4'),(3,'module_2')])
async def test_m4_decision_advances_without_web_confirm(goal_api,decision,expected):
    _,db,_=goal_api
    await seed_review(db,decision=decision)
    target,cycle=await record_steps(db,session_id='chat-a',user_id='a',module='module_4',requested_target='module_4',steps=list(MODULE_STEP_KEYS['module_4']),assistant_message_id=20)
    assert target==expected and cycle!='reviewed-cycle'
    logs=schema.tables['ai_decision_logs']
    log=(await db.execute(select(logs).where(logs.c.decision_type=='user_confirmation'))).mappings().one()
    assert log['evidence_message_ids']==[18] and log['decision_value']['source']=='dialogue'
    assert (await db.execute(select(func.count()).select_from(ConversationMessage))).scalar_one()==10


@pytest.mark.asyncio
@pytest.mark.parametrize('fresh', [True, False])
async def test_m2_to_m3_to_execution_uses_real_extraction_and_chat_only(goal_api, fresh):
    _,db,_=goal_api
    await stage_m2(db)
    await consent_turn(db)
    assert (await record_steps(db,session_id='chat-a',user_id='a',module='module_2',requested_target='module_3',
        steps=list(MODULE_STEP_KEYS['module_2']),assistant_message_id=22))[0]=='module_3'
    agreement='每天在记录今日中填写活动时间、活动内容和活动后心情'
    await db.execute(insert(ConversationMessage), [
        {'id':23,'conversation_id':1,'position':4,'role':'user','content':'每天记录一次就好'},
        {'id':24,'conversation_id':1,'position':5,'role':'assistant','content':agreement+'，这个记录方式可以吗？'}])
    await db.commit()
    maker=async_sessionmaker(db.bind,expire_on_commit=False)
    payload={'negotiated_record_plan':agreement,'ai_record_requirement':'每天记录',
        'difficulty_feedback_mechanism':'遇到困难可以回来聊','_source_session_id':'chat-a',
        '_source_assistant_message_id':24}
    record_id=await persist_record(maker,module='module_3',user_id='a',data=payload,cycle_id='m2-cycle')
    assert record_id
    await record_steps(db,session_id='chat-a',user_id='a',module='module_3',requested_target='module_3',
        steps=list(MODULE_STEP_KEYS['module_3']),assistant_message_id=24)
    await db.execute(insert(ConversationMessage), [
        {'id':25,'conversation_id':1,'position':6,'role':'user','content':'我同意这样记录'},
        {'id':26,'conversation_id':1,'position':7,'role':'assistant','content':'我们按这个方式试试。'}])
    await db.commit()
    if fresh:
        await persist_record(maker,module='module_3',user_id='a',cycle_id='m2-cycle',
            data={**payload,'user_acceptance_feeling':'愿意尝试','_source_assistant_message_id':26})
    result=await record_steps(db,session_id='chat-a',user_id='a',module='module_3',requested_target='module_4',
        steps=list(MODULE_STEP_KEYS['module_3']),assistant_message_id=26)
    assert result[0]==('module_4' if fresh else 'module_3')
    row=(await db.execute(select(schema.tables['module_three_record']).where(schema.tables['module_three_record'].c.id==record_id))).mappings().one()
    assert row['record_status']==('confirmed' if fresh else 'draft')
    if fresh:
        assert row['confirmation_message_id']==25
        from app.m4_contract import cycle_messages
        messages=(await db.execute(select(ConversationMessage).order_by(ConversationMessage.position))).scalars().all()
        assert await cycle_messages(db,messages)==[]  # prior-cycle assistant reply is excluded too
