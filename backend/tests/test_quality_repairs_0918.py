"""Regression and counterexample coverage for the 0918 audit findings."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select, func, update

from app.answer_validator import validate_answer
from app.goal_contract import proposal_evidence
from app.prompts import build_system_prompt, build_system_segments
from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.v2_workflow import create_goal_from_agent_dialogue, runtime_for
from test_goal_overview import goal_api
from test_goal_contract_0914 import seed_goal_dialogue, primary_payload


def proposal(body, activity, selection=None, previous=None):
    messages = []
    if previous:
        messages.append(SimpleNamespace(id=1, position=1, role='assistant', content=previous, conversation_id=7))
    messages.append(SimpleNamespace(id=2, position=2, role='user', content=body, conversation_id=7))
    return proposal_evidence({'goal_kind': 'secondary', 'selection_quote': selection or body,
                              'activity_quote': activity}, messages, activity)


@pytest.mark.parametrize('body,activity,selection', [
    ('助手说你可以站桩', '站桩', None),
    ('我做过散步，今天也只是想起它', '散步', None),
    ('我选择不散步', '不散步', None),
    ('我选择不散步', '散步', None),
    ('我决定不去散步', '散步', None),
    ('我想不散步', '散步', None),
    ('如果我选择散步会怎么样？', '散步', '我选择散步'),
    ('朋友说我想试试散步', '散步', '我想试试散步'),
    ('我以前说过我想试试散步', '散步', '我想试试散步'),
    ('我想试试散步这句话只是一个例子', '散步', '我想试试散步'),
    ('我想试试散步？我还没决定', '散步', '我想试试散步'),
    ('我选择散步，但先别创建目标', '散步', '我选择散步'),
    ('我选择散步，算了先不做了', '散步', '我选择散步'),
    ('我不想跑步，但我想游泳', '跑步', '我想游泳'),
    ('我想试试游泳', '跑步', None),
    ('我想试试“散步”，但这只是引用', '散步', None),
    ('朋友说：\n我选择散步', '散步', '我选择散步'),
    ('朋友说：“好的，我选择散步”', '散步', '我选择散步'),
    ('我不想跑步，但我选择游泳', '游泳', '我不想跑步'),
    ('我想试试散步或者游泳', '散步', None),
    ('我选择散步还是游泳好呢', '游泳', '我选择散步还是游泳'),
])
def test_goal_rejects_non_selection_and_cherry_picked_quotes(body, activity, selection):
    assert proposal(body, activity, selection) is None


@pytest.mark.parametrize('body,activity,previous', [
    ('我选择每天晚饭后散步', '每天晚饭后散步', None),
    ('我自己选择散步作为主要目标', '散步', None),
    ('这次自己选择散步作为主要目标', '散步', None),
    ('我这次亲自选择站桩作为次要目标', '站桩', None),
    ('我想试试站桩', '站桩', None),
    ('我不想跑步，但我选择游泳', '游泳', None),
    ('我以前做过散步，现在我想继续散步', '散步', None),
    ('那就散步吧', '散步', None),
    ('我选“游泳”', '游泳', None),
    ('我选择散步，时间还没决定', '散步', None),
    ('我选择散步，但我还没决定具体时间', '散步', None),
    ('朋友建议跑步，我选择游泳', '游泳', None),
    ('我还是选择散步', '散步', None),
    ('好，就按这个试试', '晚饭后散步', '你愿意选择晚饭后散步作为想试的活动吗？'),
    ('散步', '散步', '你想选择哪种活动？'),
])
def test_goal_keeps_explicit_positive_choices(body, activity, previous):
    assert proposal(body, activity, previous=previous) is not None


def test_goal_accepts_grounded_normalized_activity_content():
    """A short source label may be normalized into a more specific plan title.

    The old contract required ``activity_quote`` to be a substring of the
    display activity, so a user saying ``散步`` while specifying
    ``小区平路慢走五分钟`` could never create a goal.  Both values still have
    to be literal spans of the user's turn (or the immediately preceding
    suggestion); only the substring relationship is relaxed.
    """
    body = "我选择散步，具体是在小区平路慢走五分钟。"
    messages = [SimpleNamespace(id=2, position=2, role="user", content=body,
                                conversation_id=7)]
    result = proposal_evidence({"goal_kind": "secondary", "selection_quote": "我选择散步",
        "activity_quote": "散步"}, messages, "小区平路慢走五分钟")
    assert result is not None
    assert result["evidence"]["activity_quote"] == "散步"
    assert result["source_activity"] == "小区平路慢走五分钟"


def test_goal_rejects_ungrounded_normalized_activity_content():
    body = "我选择散步。"
    messages = [SimpleNamespace(id=2, position=2, role="user", content=body,
                                conversation_id=7)]
    assert proposal_evidence({"goal_kind": "secondary", "selection_quote": "我选择散步",
        "activity_quote": "散步"}, messages, "每天游泳三十分钟") is None


def test_goal_selection_can_be_confirmed_after_later_plan_detail_turns():
    """A delayed extractor may see the card-confirmation turn last.

    The choice is still the user's earlier explicit selection.  Requiring the
    quote to occur in only the final user message made this ordinary sequence
    impossible to create a goal from.
    """
    messages = [
        SimpleNamespace(id=1, position=1, role="user", conversation_id=7,
                        content="我选择散步作为这周要尝试的活动。"),
        SimpleNamespace(id=2, position=2, role="assistant", conversation_id=7,
                        content="好的，我们再把时间、地点和时长具体化。"),
        SimpleNamespace(id=3, position=3, role="user", conversation_id=7,
                        content="每天晚饭后七点，在小区楼下走十分钟。"),
        SimpleNamespace(id=4, position=4, role="assistant", conversation_id=7,
                        content="计划卡：每天晚饭后七点，在小区楼下散步十分钟。"),
        SimpleNamespace(id=5, position=5, role="user", conversation_id=7,
                        content="这张卡没问题，我同意按这个计划试。"),
    ]
    result = proposal_evidence({"goal_kind": "secondary",
        "selection_quote": "我选择散步作为这周要尝试的活动。", "activity_quote": "散步"},
        messages, "每天晚饭后七点，在小区楼下散步十分钟")
    assert result is not None
    assert result["source_message_id"] == 1


def test_goal_selection_is_invalidated_by_later_replacement():
    messages = [
        SimpleNamespace(id=1, position=1, role="user", conversation_id=7,
                        content="我选择散步作为这周要尝试的活动。"),
        SimpleNamespace(id=2, position=2, role="assistant", conversation_id=7,
                        content="好的，我们再把计划具体化。"),
        SimpleNamespace(id=3, position=3, role="user", conversation_id=7,
                        content="我改成游泳，不做散步了。"),
        SimpleNamespace(id=4, position=4, role="assistant", conversation_id=7,
                        content="那我们重新整理游泳计划。"),
        SimpleNamespace(id=5, position=5, role="user", conversation_id=7,
                        content="确认这张散步卡。"),
    ]
    assert proposal_evidence({"goal_kind": "secondary",
        "selection_quote": "我选择散步作为这周要尝试的活动。", "activity_quote": "散步"},
        messages, "散步") is None


def test_goal_card_cannot_add_a_second_activity_to_the_user_choice():
    messages = [
        SimpleNamespace(id=1, position=1, role="user", conversation_id=7,
                        content="我选择散步作为这周要尝试的活动。"),
        SimpleNamespace(id=2, position=2, role="assistant", conversation_id=7,
                        content="目标卡：散步并跑步，每天十分钟。"),
        SimpleNamespace(id=3, position=3, role="user", conversation_id=7,
                        content="确认这张卡。"),
    ]
    result = proposal_evidence({"goal_kind": "secondary",
        "selection_quote": "我选择散步作为这周要尝试的活动。", "activity_quote": "散步"},
        messages, "散步并跑步")
    # The card-only expansion may be observed for diagnostics, but the
    # creator must persist the source-bound user activity instead.
    assert result and result["source_activity"] == "散步"


def test_goal_selection_survives_a_long_detail_transcript_but_not_reconsideration():
    messages = [SimpleNamespace(id=1, position=1, role="user", conversation_id=7,
                                content="我选择散步作为这周要尝试的活动。"),
                SimpleNamespace(id=2, position=2, role="assistant", conversation_id=7,
                                content="好的，继续具体化。")]
    for index in range(3, 44):
        role = "user" if index % 2 else "assistant"
        messages.append(SimpleNamespace(id=index, position=index, role=role,
                                        conversation_id=7,
                                        content="我补充一些时间地点细节。" if role == "user"
                                                else "我记下了这些细节。"))
    messages.extend([
        SimpleNamespace(id=44, position=44, role="assistant", conversation_id=7,
                        content="计划卡：散步，每天十分钟。"),
        SimpleNamespace(id=45, position=45, role="user", conversation_id=7,
                        content="确认这张卡。"),
    ])
    accepted = proposal_evidence({"goal_kind": "secondary",
        "selection_quote": "我选择散步作为这周要尝试的活动。", "activity_quote": "散步"},
        messages, "散步")
    assert accepted and accepted["source_message_id"] == 1

    messages.insert(-1, SimpleNamespace(id=43.5, position=43.5, role="user",
                                        conversation_id=7, content="我再考虑一下，先不做这个目标。"))
    assert proposal_evidence({"goal_kind": "secondary",
        "selection_quote": "我选择散步作为这周要尝试的活动。", "activity_quote": "散步"},
        messages, "散步") is None


def test_later_friend_suggestion_does_not_replace_the_user_choice():
    messages = [
        SimpleNamespace(id=1, position=1, role="user", conversation_id=7,
                        content="我选择散步作为这周要尝试的活动。"),
        SimpleNamespace(id=2, position=2, role="assistant", conversation_id=7,
                        content="好的，我们把计划具体化。"),
        SimpleNamespace(id=3, position=3, role="user", conversation_id=7,
                        content="朋友说也可以换成游泳，不过我还在考虑时间。"),
        SimpleNamespace(id=4, position=4, role="assistant", conversation_id=7,
                        content="计划卡：散步，每天十分钟。"),
        SimpleNamespace(id=5, position=5, role="user", conversation_id=7,
                        content="确认这张卡。"),
    ]
    result = proposal_evidence({"goal_kind": "secondary",
        "selection_quote": "我选择散步作为这周要尝试的活动。", "activity_quote": "散步"},
        messages, "散步")
    assert result and result["source_message_id"] == 1


@pytest.mark.parametrize('body,previous', [
    ('好', '你可以散步或游泳，你喜欢哪一个？'),
    ('好', '刚才我只是在举例：你可以散步。你理解PA了吗？'),
    ('我知道了', '你愿意选择散步作为目标吗？'),
    ('好，但我还没决定', '你愿意选择散步作为目标吗？'),
])
def test_goal_ambiguous_acknowledgment_never_selects_assistant_activity(body, previous):
    assert proposal(body, '散步', previous=previous) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('body,activity', [('助手说你可以站桩','站桩'), ('我做过散步，今天只是想起它','散步'), ('我选择不散步','不散步')])
async def test_invalid_goal_writes_no_goal_plan_cycle_or_audit(goal_api, body, activity):
    _, db, _ = goal_api
    aid = await seed_goal_dialogue(db)
    await db.execute(update(ConversationMessage).where(ConversationMessage.id == 102).values(content=body))
    await db.commit()
    tables = [schema.tables[name] for name in ('pa_goals','module_two_record','pa_cycles','ai_decision_logs')]
    before = [(await db.execute(select(func.count()).select_from(t))).scalar_one() for t in tables]
    data = {**primary_payload(), 'target_activity_content': activity,
            'goal_proposal': {'goal_kind':'secondary','selection_quote':body,'activity_quote':activity}}
    assert await create_goal_from_agent_dialogue(db, session_id='new-chat-a', user_id='a', data=data,
        completed_steps=['activity_selected','values_or_intention_explored'], assistant_message_id=aid) is None
    await db.commit()
    after = [(await db.execute(select(func.count()).select_from(t))).scalar_one() for t in tables]
    assert before == after
    _, state = await runtime_for(db, 'new-chat-a')
    assert state['active_goal_id'] is None and state['current_module'] == 'module_2'


BAD_REPLIES = [
    ('module_1', '我们可以把每天散步三十分钟作为目标，明天开始试试。'),
    ('module_1', '你这是在回避，所以才一直没有行动。'),
    ('module_1', '你可能患有抑郁症，建议按抑郁症来处理。'),
    ('module_1', '只要你行动起来，心情一定会马上变好。'),
    ('module_2', '那我们就定成每天晚饭后跑步三十分钟吧。'),
    ('module_2', '你的目标已经确定为每天跑步。'),
    ('module_2', '根据系统规则，我已经把你的目标写入数据库并切换了路由。'),
    ('module_2', '请打开目标总览，确认并保存这个目标。'),
    ('module_2', '不要担心，请去目标面板确认目标。'),
    ('module_2', '如果你同意，请去目标面板确认目标。'),
    ('module_2', '现在正式进入模块三，开始记录。'),
    ('module_3', '你没做活动是因为反刍和回避，我们来分析这个循环。'),
    ('module_3', '我们把目标改成游泳吧，原来的计划不用管了。'),
    ('module_4', '你没有出门是因为你在回避，这形成了负性循环。'),
    ('module_4', '你应该坚持每天跑步，不要再找借口。'),
    ('module_4', '这次计划执行得很好，已经完成了安排。'),
    ('module_4', '你今天完成了站桩，感觉一定更开心。'),
]


@pytest.mark.parametrize('module,reply', BAD_REPLIES)
def test_known_violations_are_blocked_not_merely_reviewed(module, reply):
    result = validate_answer(reply=reply, module=module, evidence_ids=[], workflow={
        'available':True, 'current_module':module, 'goal_selected':False, 'plan_confirmed':False})
    assert result['status'] == 'blocked', result
    assert result['llm_calls'] == 0 and not result['semantic_verified']


@pytest.mark.parametrize('module,reply', [
    ('module_1','行动不一定让心情马上变好，我们可以观察后再调整。'),
    ('module_1','心情不一定会马上变好，我们可以观察后再调整。'),
    ('module_1','我们不需要现在设定目标，先理解经历。'),
    ('module_1','比如有人会通过散步观察情绪变化，这只是一个BA的例子。'),
    ('module_1','你说昨天散步后心情好了些，我理解得对吗？'),
    ('module_1','我不能判断你是否患有抑郁症，可以找专业人员评估。'),
    ('module_1','你说自己在回避，这个词对你意味着什么？'),
    ('module_2','我们不需要去目标面板确认，在这里聊就好。'),
    ('module_2','目标总览只用于回顾历史，确认在聊天中完成。'),
    ('module_2','你愿意尝试散步吗？这只是一个候选活动，由你来决定。'),
    ('module_2','你问怎么选目标：可以先聊想尝试什么。'),
    ('module_3','你可以打开记录今日填写活动时间、内容和心情。'),
    ('module_4','你觉得这次没出门和回避有关吗？'),
    ('module_4','你完成了今天的目标吗？'),
    ('module_4','如果你已经完成今天的目标，可以说说实际感受。'),
    ('module_4','这不代表你就是懒，我们先看具体发生了什么。'),
    ('module_4','不要说“不要再找借口”，我们可以先了解当时的困难。'),
])
def test_education_questions_denials_and_user_autonomy_are_not_blocked(module, reply):
    result = validate_answer(reply=reply,module=module,evidence_ids=[],workflow={
        'available':True,'current_module':module,'plan_confirmed':False})
    assert result['status'] != 'blocked', result


@pytest.mark.parametrize('builder', [build_system_prompt, build_system_segments])
def test_daily_instructions_are_current_even_with_admin_override(builder):
    raw = builder('module_3', module_prompt='管理员自定义记录话术')
    prompt = raw if isinstance(raw,str) else '\n'.join(s.text for s in raw)
    assert '管理员自定义记录话术' in prompt
    for text in ('左侧','记录今日','查看历史','想做的事情完成程度','整体心情','4项选填'):
        assert text in prompt
    for text in ('右上角的笔记本','我的每日记录','整体／平均心情','没有预定计划可选'):
        assert text not in prompt


def test_m4_bound_secondary_and_unbound_activity_are_distinct():
    prompt = build_system_prompt('module_4')
    assert '独立 secondary 目标被选中后同样享有完整复盘' in prompt
    assert '不对次要目标进行独立的数据收集' not in prompt
    assert '对次要目标进行独立、完整的复盘' not in prompt


@pytest.mark.parametrize('module', ['module_1','module_2','module_3','module_4'])
def test_flat_and_segmented_prompts_have_identical_content(module):
    assert build_system_prompt(module) == '\n\n'.join(s.text for s in build_system_segments(module))


@pytest.mark.parametrize('user,reply,blocked', [
    ('我今天完成了站桩', '你今天完成了站桩。', False),
    ('我今天没完成站桩', '你今天完成了站桩。', True),
    ('他今天完成了站桩', '你今天完成了站桩。', True),
    ('我今天完成了站桩', '你今天完成了游泳。', True),
    ('我今天完成了站桩', '你今天完成了站桩，感觉一定更开心。', True),
    ('我只是想试试站桩', '如果你完成了站桩，可以说说感受。', False),
    ('我今天完成了站桩', '你今天完成了站桩，感觉怎么样？', False),
    ('今天没有做活动', '你今天完成了站桩，感觉怎么样？', True),
])
def test_completion_claim_requires_current_self_report(user, reply, blocked):
    result = validate_answer(reply=reply,module='module_4',evidence_ids=[],current_user=user)
    assert (result['status'] == 'blocked') is blocked


def test_conversational_card_confirmation_is_not_a_panel_instruction():
    result = validate_answer(reply='这是我们在聊天中整理的目标卡片，你愿意确认这个安排吗？',
                             module='module_2', evidence_ids=[])
    assert result['status'] == 'passed'


@pytest.mark.parametrize('module,reply', BAD_REPLIES)
@pytest.mark.parametrize('stream', [False, True])
async def test_bad_drafts_never_leak_or_drive_workflow(context, provider, monkeypatch, module, reply, stream):
    import dataclasses
    from app.graph import get_graph
    from app.graph import nodes
    from app.providers.base import Completion, StreamDelta

    async def generated(**kwargs):
        return Completion(text=reply, model='synthetic', reasoning_content='rejected-draft-reasoning')
    async def generated_stream(**kwargs):
        yield StreamDelta(kind='reasoning', text='rejected-draft-reasoning')
        # Chunk boundaries must not bypass the shared guard.
        for chunk in reply:
            yield StreamDelta(kind='content', text=chunk)
    def unexpected_job(coro):
        coro.close()
        pytest.fail('a rejected draft started a background workflow job')
    monkeypatch.setattr(provider,'complete',generated)
    monkeypatch.setattr(provider,'stream',generated_stream)
    monkeypatch.setattr(nodes,'_spawn_background',unexpected_job)
    ctx = dataclasses.replace(context,stream=stream)
    events, final = [], None
    async for kind, value in get_graph().astream({'user_input':'先不要替我决定', 'forced_module':module},
            context=ctx, stream_mode=['custom','values']):
        if kind == 'custom': events.append(value)
        else: final = value
    assert not final.get('error') and final['reply_held']
    assert final['next_module'] == module and final['routing_pending'] is False
    assert final['reasoning_content'] == '' and final['final_response'] != reply
    validation = final['telemetry']['answer_validator']
    assert validation['original_status'] == 'blocked' and validation['status'] == 'corrected'
    assert validation['progression_held'] and validation['llm_calls'] == 0
    if stream:
        assert ''.join(e['text'] for e in events if e['type'] == 'delta') == final['final_response']
    assert not any(e['type'] == 'reasoning_delta' for e in events)
    assert any(e['type'] == 'done' for e in events)
    saved = await context.store.get(final['session_id'])
    assert saved.messages[-1].content == final['final_response']
    nodes.schedule_background_routing(final,ctx,assistant_message_id=None)
    # Defense in depth even when a direct caller incorrectly sets pending=True.
    nodes.schedule_background_routing({**final,'routing_pending':True},ctx,assistant_message_id=None)


@pytest.mark.parametrize('stream', [False,True])
def test_recovery_is_a_completed_http_turn_not_a_502(client, provider, monkeypatch, stream):
    from app.providers.base import Completion, StreamDelta
    from test_chat import sse_events
    bad = '从明天开始你应该每天跑步。'
    async def generated(**kwargs): return Completion(text=bad,model='synthetic')
    async def generated_stream(**kwargs): yield StreamDelta(kind='content',text=bad)
    monkeypatch.setattr(provider,'complete',generated)
    monkeypatch.setattr(provider,'stream',generated_stream)
    response = client.post('/api/chat/stream' if stream else '/api/chat',
                           json={'message':'我最近感觉有点烦', 'module':'module_1'})
    assert response.status_code == 200 and bad not in response.text
    if stream:
        events = sse_events(response.text)
        done = next(data for name,data in events if name == 'done')
        assert not any(name == 'error' for name,_ in events)
    else:
        done = response.json()
        assert '暂时没有展示' in done['reply']
    assert done['next_module'] == 'module_1' and done['routing_pending'] is False


async def test_correction_audit_persists_without_the_rejected_draft(client, auth_headers, provider, monkeypatch, db_sessionmaker):
    from app.models import AIExecutionEvent
    from app.providers.base import Completion
    bad = '从明天开始你应该每天跑步。'
    async def generated(**kwargs):
        return Completion(text=bad,model='synthetic',reasoning_content='rejected-reasoning')
    monkeypatch.setattr(provider,'complete',generated)
    response = client.post('/api/chat',json={'message':'我最近感觉有点烦'},headers=auth_headers)
    assert response.status_code == 200, response.text
    async with db_sessionmaker() as db:
        event = (await db.execute(select(AIExecutionEvent).where(
            AIExecutionEvent.session_id == response.json()['session_id'],
            AIExecutionEvent.stage == 'main_generation'))).scalar_one()
        validator = event.event_metadata['answer_validator']
        assert validator['status'] == 'corrected' and validator['progression_held']
        assert validator['findings'] == [{'code':'premature_plan','severity':'block'}]
        messages = (await db.execute(select(ConversationMessage).where(
            ConversationMessage.conversation_id == event.conversation_id))).scalars().all()
        assert all(bad not in m.content and not m.reasoning_content for m in messages)
        assert bad not in str(event.event_metadata) and 'rejected-reasoning' not in str(event.event_metadata)


async def test_held_turn_cannot_start_jobs_with_a_subject(context, monkeypatch):
    from langgraph.runtime import Runtime
    from app.graph import nodes
    def unexpected(coro):
        coro.close()
        pytest.fail('held turn started an extraction/memory/router job')
    monkeypatch.setattr(nodes,'_spawn_background',unexpected)
    state = {'subject_id':'synthetic', 'session_id':'isolated', 'reply_held':True,
             'extracted_intent':'module_2','next_module':'module_3','user_input':'test'}
    assert await nodes.summarizer_node(state,Runtime(context=context)) == {}
    nodes._dispatch_transition_jobs(state,context,current='module_2',target='module_3')
    await nodes._run_background_routing(state,context,assistant_message_id=None)
