import dataclasses
import pytest
from app.answer_validator import validate_answer
from app.reply_workflow import truthful_workflow_reply
from app.graph import get_graph
from app.providers.base import StreamDelta

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
