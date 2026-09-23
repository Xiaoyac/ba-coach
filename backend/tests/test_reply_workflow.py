import dataclasses
import pytest
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.answer_validator import validate_answer
from app.database_v2_schema import metadata as schema
from app.reply_workflow import read_reply_workflow, truthful_workflow_reply, workflow_prompt
from app.graph import get_graph
from app.providers.base import StreamDelta
from test_goal_overview import goal_api

PENDING={'available':True,'current_module':'module_2','plan_confirmed':False}
BAD='收到确认，目标卡片已锁定。接下来进入模块三，我们一起把计划落实到行动中。'

@pytest.mark.parametrize('reply',[BAD,'计划已保存。','现在切换到模块三。','我们已经进入M3。'])
def test_pending_rejects_state_claim(reply):
    assert validate_answer(reply=reply,module='module_2',evidence_ids=[],workflow=PENDING)['status']=='blocked'

@pytest.mark.parametrize('reply',['你在聊天里同意了，网页确认成功后才能进入下一步。','计划尚未提交，请先核对。','如果确认后进入模块三，我们再讨论记录。'])
def test_conditional_not_claim(reply):
    assert validate_answer(reply=reply,module='module_2',evidence_ids=[],workflow=PENDING)['status']!='blocked'

def test_real_confirmed_state_allows_claim():
    assert validate_answer(reply=BAD,module='module_3',evidence_ids=[],workflow={**PENDING,'current_module':'module_3','plan_confirmed':True})['status']=='passed'

@pytest.mark.parametrize('stream',[True,False])
async def test_authority_corrects_before_delivery(context,provider,monkeypatch,stream):
    async def authority(*args): return PENDING
    monkeypatch.setattr('app.reply_workflow.read_reply_workflow',authority)
    async def bad_stream(**kwargs): yield StreamDelta(kind='content',text=BAD)
    monkeypatch.setattr(provider,'stream',bad_stream)
    # Stub complete implementation derives its reply from this field.
    async def bad_complete(**kwargs):
        from app.providers.base import Completion
        return Completion(text=BAD,model='synthetic')
    monkeypatch.setattr(provider,'complete',bad_complete)
    context=dataclasses.replace(context,settings=context.settings.model_copy(update={'database_schema_version':'v2'}),stream=stream)
    events=[]; final=None
    async for kind,value in get_graph().astream({'user_input':'请确认计划','forced_module':'module_2','subject_id':'synthetic'},context=context,stream_mode=['custom','values']):
        if kind=='custom': events.append(value)
        else: final=value
    assert final['final_response']==truthful_workflow_reply(PENDING)
    assert final['telemetry']['answer_validator']['status']=='corrected'
    assert BAD not in ''.join(e.get('text','') for e in events if e.get('type')=='delta')


@pytest.mark.asyncio
async def test_reply_authority_reads_fresh_m1_dialogue_status(goal_api):
    _, db, _ = goal_api
    contract = {
        'version': 'm1-20260914-v1', 'session_id': 'chat-a',
        'path': 'personalized', 'missing_fields': ['ba_understanding', 'goal_setting_consent'],
        'completed_steps': ['core_problem_example', 'depression_cycle_formulated'],
        'milestones': {'m1_milestone_1': True, 'm1_milestone_2': True, 'm1_milestone_3': False},
        'education_missing_topics': ['情绪、精力和行动相互影响'],
        'education_evidence_complete': False, 'understanding_verified': False,
        'goal_consent_expressed': True, 'core_questions_resolved': True,
    }
    await db.execute(update(schema.tables['conversation_runtime_states']).where(
        schema.tables['conversation_runtime_states'].c.conversation_id == 1).values(
            current_module='module_1', flow_status='active', active_goal_id=None,
            active_cycle_id=None, last_transition_reason='discussion_required'))
    await db.execute(insert(schema.tables['module_one_record']), {
        'id': 'm1-reply-status', 'user_id': 'a', 'version_no': 1,
        'record_status': 'draft', 'event_experience': {'schema_version': 2, '_m1_contract': contract},
    })
    await db.commit()
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    authority = await read_reply_workflow(maker, 'a', 'chat-a')
    assert authority['m1_status']['missing_fields'] == ['ba_understanding', 'goal_setting_consent']
    assert authority['m1_status']['education_missing_topics'] == ['情绪、精力和行动相互影响']
    assert authority['m4_status'] is None


def test_workflow_prompt_requires_real_m1_education_before_consent_or_activity():
    prompt = workflow_prompt({
        'available': True, 'current_module': 'module_1',
        'm1_status': {
            'missing_fields': ['ba_understanding', 'goal_setting_consent'],
            'education_missing_topics': ['情绪、精力和行动相互影响', '行动不保证立刻开心'],
            'goal_consent_expressed': True,
        },
    })
    assert '逐项补充 education_missing_topics' in prompt
    assert '不能只说“我收到/你已理解”' in prompt
    assert '不能跳去问具体活动或重复索取目标设定同意' in prompt


def test_workflow_prompt_exposes_m4_first_missing_evidence_action():
    prompt = workflow_prompt({
        'available': True, 'current_module': 'module_4', 'flow_status': 'waiting_execution',
        'm4_status': {
            'missing_fields': ['m4_milestone_3', 'm4_milestone_5'],
            'completed_steps': ['execution_reviewed', 'abc_chain_completed'],
            'review_decision': 4, 'next_action': '先补实际提供的BA教育及用户理解，再继续后续收尾。',
        },
    })
    assert 'M4最新证据状态' in prompt
    assert '按 next_action 只补当前首个缺项' in prompt
    assert '用户已说结束/暂停也不能跳过前置证据' in prompt
