"""Isolated database regressions: no production data, panels, or provider calls."""
import json
import pytest
from sqlalchemy import insert, select, update, func
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.v2_workflow import record_steps, runtime_for, persist_record
from app.workflow_contract import MODULE_STEP_KEYS
from app.dialogue_confirmation import (
    affirmative,
    anchor_rendered_summary,
    confirmation_fields_complete,
    confirmation_marker_matches,
    precommit_user_confirmation,
    fingerprint,
    record_hash,
    render_confirmation_summary,
    summary_present,
)
from app.answer_validator import validate_answer
from test_goal_overview import goal_api
from test_m1_program_0914 import seed as seed_m1
from test_extraction_freshness_0916 import seed_m2
from test_cycle_program_0914 import seed_review


@pytest.mark.parametrize('text', ['好的', '我愿意按这个计划试试', '好的，我觉得这个安排挺合适的，就按这个来',
    '我确认并同意这个记录方法', '可以，就按刚才这个方式记录', '对，你整理的记录方式准确，我确认',
    '我同意刚才的完整计划', '确认，就按这个计划试试',
    '这个记录方式我能做到，就这么安排吧', '这个记录方法我可以完成，按这个来',
    '没错，就是这样', '没错，按这个来'])
def test_scoped_consent_language(text):
    assert affirmative(text)


@pytest.mark.parametrize('text', ['不同意', '好的，但是改成20分钟', '我愿意吗？', '他说“我同意”', '先等等', '我同意，不过下雨就不做了',
    '我不太同意这样记录', '朋友说就按这个来', '我同意这样记录，但每小时一次',
    '我同意这样记录，改为每小时一次', '我并不愿意这样记录', '我同意这样记录，时间是明天八点',
    '假设我同意这样记录', '我还不能确认并同意这个记录方法', '记录方式不准确，我确认',
    '这个计划看着可以，其实我没决定', '同意这样记录只是举例', '好的，我先想想'])
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


class SemanticConfirmationStub:
    def __init__(self, quote):
        self.quote = quote
        self.calls = []

    async def route(self, *, system, user, max_tokens=None):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return json.dumps({"intent": "confirm", "source_quote": self.quote,
                           "amendment": False, "unresolved": False}, ensure_ascii=False)


def test_m3_recording_agreement_changes_still_invalidate_fingerprint():
    record = {"negotiated_record_plan": {"text": "每天记录一次"}}
    changed = {"negotiated_record_plan": {"text": "每小时记录一次"}}
    assert fingerprint(record) != fingerprint(changed)


def test_m3_empty_recording_plan_cannot_become_confirmation_anchor():
    incomplete = {
        "record_requirement": "记录活动内容和心情",
        "negotiated_record_plan": {"text": ""},
        "feedback_mechanism": "遇到困难回来聊",
    }
    assert not confirmation_fields_complete("module_3", incomplete)
    marker = {
        "module": "module_3", "cycle_id": "cycle-1", "assistant_message_id": 10,
        "field_complete": True, "summary_verified": True,
        "snapshot": {"record_requirement": "记录活动内容和心情",
                      "negotiated_record_plan": {"text": ""},
                      "feedback_mechanism": "遇到困难回来聊"},
        "fingerprint": fingerprint(incomplete),
    }
    assert not confirmation_marker_matches(
        "module_3", marker, incomplete, cycle_id="cycle-1",
        preceding_assistant_id=10,
    )


def test_m3_summary_renderer_uses_proposed_contract_fields():
    record = {
        "record_requirement": "活动后记录内容和心情",
        "negotiated_record_plan": {"schema_version": 1, "text": "每天在记录今日填写一次"},
        "feedback_mechanism": "遇到困难回来聊",
    }
    card = render_confirmation_summary("module_3", record)
    assert card is not None
    assert "活动后记录内容和心情" in card
    assert "每天在记录今日填写一次" in card
    assert "遇到困难回来聊" in card
    assert summary_present("module_3", card, record)


def test_plan_card_confirmation_uses_executable_fields_not_narrative_rewrite():
    record = {
        "activity_content": "办公室走廊走十分钟", "schedule_text": "每周一到周五做，先试一周",
        "scheduled_start_at": "2026-09-21T12:30:00", "timezone": "Asia/Shanghai",
        "location": "办公室走廊", "duration_minutes": 10,
        "frequency_rule": {"schema_version": 1, "text": "每周一到周五做，先试一周"},
        "potential_barriers": ["临时工作多"],
        "barrier_coping_plan": [{"barrier": "临时工作多", "plan": "就先走三分钟，不勉强"}],
        "core_values_impact": "原始说法",
    }
    rewritten = {**record, "core_values_impact": "模型重新整理后的同义说法"}
    assert fingerprint(record) == fingerprint(rewritten)
    card = ("内容：办公室走廊走十分钟；时间：2026年9月21日12:30；"
            "地点：办公室走廊；时长：10分钟；频率：每周一到周五做，先试一周；"
            "潜在障碍：临时工作多；应对：就先走三分钟，不勉强。这个安排你愿意按计划试试吗？")
    assert summary_present("module_2", card, record)
    assert not summary_present("module_2", card.replace("2026年", "2025年"), record)
    assert not summary_present("module_2", card.replace("12:30", "11:30") + "其他编号1230", record)
    assert not summary_present("module_2", card.replace("12:30", "112:30"), record)


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
@pytest.mark.parametrize('case', ['success','correction','stale','changed_draft','other_user','late_router',
    'qualified_refusal','reported_choice','implicit_amendment'])
async def test_m2_requires_same_plan_current_evidence_and_real_consent(goal_api,case):
    _,db,_ = goal_api
    await stage_m2(db)
    text = {'correction':'好的，但是改成20分钟', 'qualified_refusal':'我不太同意这样记录',
        'reported_choice':'朋友说就按这个来', 'implicit_amendment':'就按这个计划试试，但每小时一次'}.get(case, '就按这个计划试试')
    await consent_turn(db, text, fresh=case!='stale')
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


@pytest.mark.asyncio
async def test_m3_confirmation_only_keeps_displayed_record_agreement_after_rewrite(goal_api):
    """A confirmation may be phrased differently without changing the contract."""
    _, db, _ = goal_api
    await stage_m2(db)
    await consent_turn(db)
    assert (await record_steps(db, session_id='chat-a', user_id='a', module='module_2',
        requested_target='module_3', steps=list(MODULE_STEP_KEYS['module_2']),
        assistant_message_id=22))[0] == 'module_3'

    agreement = '每天在记录今日填写活动时间、活动内容和活动后心情（0到5分），其他感受选填'
    await db.execute(insert(ConversationMessage), [
        {'id': 23, 'conversation_id': 1, 'position': 4, 'role': 'user',
         'content': '我愿意每天在记录今日填写这些内容。'},
        {'id': 24, 'conversation_id': 1, 'position': 5, 'role': 'assistant',
         'content': agreement + '，这样记录可以吗？'},
    ])
    await db.commit()
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    payload = {'negotiated_record_plan': agreement, 'ai_record_requirement': agreement,
        'difficulty_feedback_mechanism': '遇到困难回来聊天', '_source_session_id': 'chat-a',
        '_source_assistant_message_id': 24}
    record_id = await persist_record(maker, module='module_3', user_id='a',
        data=payload, cycle_id='m2-cycle')
    assert record_id
    await record_steps(db, session_id='chat-a', user_id='a', module='module_3',
        requested_target='module_3', steps=list(MODULE_STEP_KEYS['module_3']),
        assistant_message_id=24)

    await db.execute(insert(ConversationMessage), [
        {'id': 25, 'conversation_id': 1, 'position': 6, 'role': 'user',
         'content': '对，你整理的记录方式准确，我确认。'},
        {'id': 26, 'conversation_id': 1, 'position': 7, 'role': 'assistant',
         'content': '好，记录约定就这样定了。'},
    ])
    await db.commit()
    rewritten = ('每天完成活动后在网页记录今日填写时间、内容和心情，心情按0–5分；'
                 '其他感受有空再补，遇到困难回来聊天')
    await persist_record(maker, module='module_3', user_id='a', cycle_id='m2-cycle',
        data={'negotiated_record_plan': rewritten, 'ai_record_requirement': rewritten,
            'difficulty_feedback_mechanism': '遇到困难随时回来聊',
            'user_acceptance_feeling': '我确认', '_source_session_id': 'chat-a',
            '_source_assistant_message_id': 26})
    row = (await db.execute(select(schema.tables['module_three_record'])
        .where(schema.tables['module_three_record'].c.id == record_id))).mappings().one()
    assert row['negotiated_record_plan']['text'] == agreement
    assert row['feedback_mechanism'] == '遇到困难回来聊天'
    result = await record_steps(db, session_id='chat-a', user_id='a', module='module_3',
        requested_target='module_4', steps=list(MODULE_STEP_KEYS['module_3']), assistant_message_id=26)
    assert result[0] == 'module_4'


@pytest.mark.asyncio
async def test_m3_confirmation_can_commit_before_reply_generation(goal_api):
    """A verified confirmation is a command and may settle before the next reply."""
    _, db, _ = goal_api
    await stage_m2(db)
    await consent_turn(db)
    assert (await record_steps(db, session_id='chat-a', user_id='a', module='module_2',
        requested_target='module_3', steps=list(MODULE_STEP_KEYS['module_2']),
        assistant_message_id=22))[0] == 'module_3'

    agreement = '每天在记录今日填写活动内容和活动后心情'
    await db.execute(insert(ConversationMessage), [
        {'id': 23, 'conversation_id': 1, 'position': 4, 'role': 'user',
         'content': '每天记录一次就好'},
        {'id': 24, 'conversation_id': 1, 'position': 5, 'role': 'assistant',
         'content': agreement + '，这个记录方式可以吗？'},
    ])
    await db.commit()
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    payload = {'negotiated_record_plan': agreement, 'ai_record_requirement': agreement,
        'difficulty_feedback_mechanism': '遇到困难回来聊', '_source_session_id': 'chat-a',
        '_source_assistant_message_id': 24}
    assert await persist_record(maker, module='module_3', user_id='a', data=payload,
        cycle_id='m2-cycle')
    await record_steps(db, session_id='chat-a', user_id='a', module='module_3',
        requested_target='module_3', steps=list(MODULE_STEP_KEYS['module_3']),
        assistant_message_id=24)
    natural_confirmation = '我看过这份完整的记录约定，内容符合我的情况，我愿意按这份安排执行。'
    await db.execute(insert(ConversationMessage), {
        'id': 25, 'conversation_id': 1, 'position': 6, 'role': 'user',
        'content': natural_confirmation})
    await db.flush()
    semantic_provider = SemanticConfirmationStub(natural_confirmation)
    result = await precommit_user_confirmation(
        db, session_id='chat-a', user_id='a', user_message_id=25,
        confirmation_provider=semantic_provider)
    await db.commit()
    assert result and result[0] == 'module_4'
    assert semantic_provider.calls
    row = (await db.execute(select(schema.tables['module_three_record'])
        .where(schema.tables['module_three_record'].c.goal_id == 'g1')
        .order_by(schema.tables['module_three_record'].c.created_at.desc()))).mappings().first()
    assert row is not None
    assert row['record_status'] == 'confirmed'
    logs = schema.tables['ai_decision_logs']
    log_sources = (await db.execute(select(logs.c.decision_value).where(
        logs.c.decision_type == 'user_confirmation'))).scalars().all()
    assert any(item['source'] == 'dialogue_precommit_semantic' for item in log_sources)


@pytest.mark.asyncio
async def test_semantic_precommit_rejects_old_plan_version_after_card_was_shown(goal_api):
    """Semantic wording cannot revive a card whose executable version changed."""
    _, db, _ = goal_api
    await stage_m2(db)
    natural_confirmation = '我看过这份完整的计划，内容符合我的情况，我愿意按这份安排执行。'
    await db.execute(insert(ConversationMessage), {
        'id': 21, 'conversation_id': 1, 'position': 2, 'role': 'user',
        'content': natural_confirmation})
    # Mutate the executable contract after the assistant card/marker was
    # written.  The confirmation must remain tied to the old fingerprint.
    plan = schema.tables['module_two_record']
    await db.execute(update(plan).where(plan.c.id == 'm2-draft').values(duration_minutes=20))
    await db.flush()
    result = await precommit_user_confirmation(
        db, session_id='chat-a', user_id='a', user_message_id=21,
        confirmation_provider=SemanticConfirmationStub(natural_confirmation))
    assert result is None
    row = (await db.execute(select(plan).where(plan.c.id == 'm2-draft'))).mappings().one()
    assert row['record_status'] == 'draft'


@pytest.mark.asyncio
async def test_semantic_precommit_rejects_runtime_change_during_provider_wait(goal_api):
    """A pause/version change while semantic confirmation is pending wins."""
    _, db, _ = goal_api
    await stage_m2(db)
    natural_confirmation = '我看过这份完整的计划，内容符合我的情况，我愿意按这份安排执行。'
    await db.execute(insert(ConversationMessage), {
        'id': 21, 'conversation_id': 1, 'position': 2, 'role': 'user',
        'content': natural_confirmation})
    await db.flush()

    class RuntimeMutatingConfirmationStub(SemanticConfirmationStub):
        def __init__(self, database, quote):
            super().__init__(quote)
            self.database = database

        async def route(self, *, system, user, max_tokens=None):
            result = await super().route(system=system, user=user, max_tokens=max_tokens)
            runtime = schema.tables['conversation_runtime_states']
            current = (await self.database.execute(select(runtime).where(
                runtime.c.conversation_id == 1))).mappings().one()
            await self.database.execute(update(runtime).where(
                runtime.c.conversation_id == 1).values(
                    flow_status='paused', row_version=current['row_version'] + 1))
            await self.database.flush()
            return result

    result = await precommit_user_confirmation(
        db, session_id='chat-a', user_id='a', user_message_id=21,
        confirmation_provider=RuntimeMutatingConfirmationStub(db, natural_confirmation))
    await db.commit()

    assert result is None
    runtime = schema.tables['conversation_runtime_states']
    state = (await db.execute(select(runtime).where(runtime.c.conversation_id == 1))).mappings().one()
    assert state['flow_status'] == 'paused'
    assert state['current_module'] == 'module_2'
    assert state['row_version'] > 0
    draft = (await db.execute(select(schema.tables['module_two_record']).where(
        schema.tables['module_two_record'].c.id == 'm2-draft'))).mappings().one()
    assert draft['record_status'] == 'draft'
    assert draft['confirmation_message_id'] is None


@pytest.mark.asyncio
async def test_rendered_summary_anchor_rejects_stale_background_reply(goal_api):
    """A late task cannot anchor an older assistant card over a newer turn."""
    _, db, _ = goal_api
    await stage_m2(db)
    await consent_turn(db)
    assert (await record_steps(db, session_id='chat-a', user_id='a', module='module_2',
        requested_target='module_3', steps=list(MODULE_STEP_KEYS['module_2']),
        assistant_message_id=22))[0] == 'module_3'
    agreement = '每天在记录今日填写活动内容和活动后心情'
    await db.execute(insert(ConversationMessage), [
        {'id': 23, 'conversation_id': 1, 'position': 4, 'role': 'user', 'content': '每天记录一次'},
        {'id': 24, 'conversation_id': 1, 'position': 5, 'role': 'assistant', 'content': ''},
    ])
    await db.commit()
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    payload = {'negotiated_record_plan': agreement, 'ai_record_requirement': agreement,
        'difficulty_feedback_mechanism': '遇到困难回来聊', '_source_session_id': 'chat-a',
        '_source_assistant_message_id': 24}
    record_id = await persist_record(maker, module='module_3', user_id='a', data=payload,
        cycle_id='m2-cycle')
    row = (await db.execute(select(schema.tables['module_three_record']).where(
        schema.tables['module_three_record'].c.id == record_id))).mappings().one()
    card = render_confirmation_summary('module_3', row)
    await db.execute(update(ConversationMessage).where(ConversationMessage.id == 24).values(content=card))
    await db.execute(insert(ConversationMessage), {
        'id': 25, 'conversation_id': 1, 'position': 6, 'role': 'assistant', 'content': '更新后的说明'})
    await db.commit()
    assert not await anchor_rendered_summary(db, session_id='chat-a', user_id='a',
        assistant_message_id=24, record_id=record_id,
        record_hash=record_hash(row))
