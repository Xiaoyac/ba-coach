"""Evidence-bound regression coverage for goal classification and activities."""
import pytest
from sqlalchemy import insert, select, update

from app.database_v2_schema import metadata as schema
from app.goal_contract import capture_activities, evidence_messages, save_goal_details, proposal_evidence, recover_goal_proposal
from app.plan_contract import missing_plan_fields, recover_one_time_frequency
from app.models import Conversation, ConversationMessage
from app.v2_workflow import create_goal_from_agent_dialogue, runtime_for
from test_goal_overview import goal_api  # shared isolated authenticated harness
from types import SimpleNamespace


def test_recover_omitted_goal_proposal_from_explicit_user_choice():
    messages = [
        SimpleNamespace(id=1, position=1, role="user", conversation_id=2, content="我希望有改变。"),
        SimpleNamespace(id=2, position=2, role="assistant", conversation_id=2, content="可以试试找朋友一起活动。"),
        SimpleNamespace(id=3, position=3, role="user", conversation_id=2,
                       content="我觉得可以，我想约马哥，和他去旁边的万达广场玩鬼抓人，下午有时间，那就周日下午吧。"),
    ]
    recovered = recover_goal_proposal(messages, "和马哥一起玩鬼抓人")
    assert recovered and recovered["activity_quote"] == "玩鬼抓人"
    evidence = proposal_evidence(recovered, messages, "和马哥一起玩鬼抓人")
    assert evidence and evidence["source_message_id"] == 3


def test_goal_choice_survives_repeated_reference_to_the_reviewed_plan():
    """Repeated create/confirm wording must not invalidate the original choice.

    The UI can ask the user to reaffirm a complete card after the first
    routing attempt.  Those turns refer to the same plan and contain no new
    activity; treating ``我选择按这份安排执行`` as a replacement made the
    source-bound evidence disappear on the retry.
    """
    messages = [
        SimpleNamespace(id=1, position=1, role="user", conversation_id=7,
                        content="我觉得可以，我想约马哥，和他去旁边的万达广场玩鬼抓人，那就周日下午吧"),
        SimpleNamespace(id=2, position=2, role="assistant", conversation_id=7,
                        content="计划卡：周日下午和马哥玩鬼抓人。"),
        SimpleNamespace(id=3, position=3, role="user", conversation_id=7,
                        content="没错，我没有要改的地方了，就这样定目标吧"),
        SimpleNamespace(id=4, position=4, role="assistant", conversation_id=7,
                        content="好的，请确认是否创建目标。"),
        SimpleNamespace(id=5, position=5, role="user", conversation_id=7,
                        content="我选择按这份安排执行，请创建目标"),
        SimpleNamespace(id=6, position=6, role="assistant", conversation_id=7,
                        content="我记下了这份安排。"),
        SimpleNamespace(id=7, position=7, role="user", conversation_id=7,
                        content="我选择按这份安排执行，请创建目标"),
    ]
    raw = {
        "goal_kind": "secondary",
        "selection_quote": messages[0].content,
        "activity_quote": "玩鬼抓人",
    }
    evidence = proposal_evidence(raw, messages, "和马哥一起玩鬼抓人")
    assert evidence and evidence["source_message_id"] == 1


def test_goal_choice_is_invalidated_when_plan_reference_also_changes_activity():
    messages = [
        SimpleNamespace(id=1, position=1, role="user", conversation_id=7,
                        content="我选择散步作为这周要尝试的活动。"),
        SimpleNamespace(id=2, position=2, role="assistant", conversation_id=7,
                        content="计划卡：散步，每天十分钟。"),
        SimpleNamespace(id=3, position=3, role="user", conversation_id=7,
                        content="我选择按这个目标执行，但把活动改成游泳。"),
    ]
    raw = {"goal_kind": "secondary", "selection_quote": messages[0].content,
           "activity_quote": "散步"}
    assert proposal_evidence(raw, messages, "散步") is None


def test_recover_short_choice_from_list_when_card_expands_the_activity():
    messages = [
        SimpleNamespace(id=1, position=1, role="user", conversation_id=7,
                        content="我希望生活更充实一点。"),
        SimpleNamespace(id=2, position=2, role="assistant", conversation_id=7,
                        content="散步、拉伸、收拾屋子，哪件对你最不费劲？"),
        SimpleNamespace(id=3, position=3, role="user", conversation_id=7,
                        content="散步"),
        SimpleNamespace(id=4, position=4, role="assistant", conversation_id=7,
                        content="计划卡：今天出门买东西，回来路上多绕楼下最近的一段，大约5分钟。"),
        SimpleNamespace(id=5, position=5, role="user", conversation_id=7,
                        content="可以呀"),
    ]
    recovered = recover_goal_proposal(messages, "今天出门买东西，回来路上多绕楼下最近的一段")
    assert recovered and recovered["activity_quote"] == "散步"
    evidence = proposal_evidence(recovered, messages,
        "今天出门买东西，回来路上多绕楼下最近的一段")
    assert evidence and evidence["source_message_id"] == 3


def test_recover_short_choice_does_not_promote_generic_agreement():
    messages = [
        SimpleNamespace(id=1, position=1, role="assistant", conversation_id=7,
                        content="这三天里，你愿意这样试一次吗？"),
        SimpleNamespace(id=2, position=2, role="user", conversation_id=7,
                        content="应该可以"),
        SimpleNamespace(id=3, position=3, role="assistant", conversation_id=7,
                        content="计划卡：今天出门买东西，回程多绕楼下最近的一段，大约5分钟。"),
    ]
    assert recover_goal_proposal(messages, "今天出门买东西，回程多绕楼下最近的一段") is None


def test_one_time_frequency_is_recovered_only_from_explicit_user_wording():
    messages = [
        SimpleNamespace(role="user", content="就今天做一下试试看吧"),
    ]
    plan = {"activity_content": "走一小段", "schedule_text": "就今天做一下试试看吧",
            "location": "家附近", "duration_minutes": 5,
            "frequency_rule": None, "potential_barriers": ["会累"],
            "barrier_coping_plan": [{"barrier": "会累", "plan": "只走五分钟"}]}
    recovered = recover_one_time_frequency(plan, messages)
    assert recovered["frequency_rule"] == {"schema_version": 1, "text": "就今天做一下试试看"}
    assert "frequency_rule" not in missing_plan_fields(recovered)


def test_single_date_does_not_infer_frequency():
    messages = [SimpleNamespace(role="user", content="明天下午六点左右")]
    plan = {"activity_content": "散步", "schedule_text": "明天下午六点左右",
            "location": "商场", "duration_minutes": 10,
            "frequency_rule": None, "potential_barriers": ["天气"],
            "barrier_coping_plan": [{"barrier": "天气", "plan": "改室内"}]}
    assert recover_one_time_frequency(plan, messages)["frequency_rule"] is None


async def seed_goal_dialogue(db, *, conversation_id=2, first_id=100):
    await db.execute(update(schema.tables["conversation_runtime_states"]).where(
        schema.tables["conversation_runtime_states"].c.conversation_id == conversation_id).values(
            current_module="module_2", active_goal_id=None, active_cycle_id=None, flow_status="active", memory={}))
    await db.execute(insert(ConversationMessage), [
        {"id": first_id, "conversation_id": conversation_id, "position": 0, "role": "user",
         "content": "我想让自己更有精力。"},
        {"id": first_id + 1, "conversation_id": conversation_id, "position": 1, "role": "assistant",
         "content": "可以考虑每天晚饭后散步。"},
        {"id": first_id + 2, "conversation_id": conversation_id, "position": 2, "role": "user",
         "content": "我选择每天晚饭后散步，想让自己更有精力；每周日复盘。"},
        {"id": first_id + 3, "conversation_id": conversation_id, "position": 3, "role": "assistant",
         "content": "我会把这项选择整理成计划草稿。"},
    ])
    await db.commit()
    return first_id + 3


def primary_payload():
    return {
        "target_activity_content": "每天晚饭后散步",
        "schedule_text": "每天晚饭后", "target_activity_location": "小区",
        "target_activity_duration_minutes": 10,
        "goal_proposal": {"goal_kind": "primary", "long_term_direction": "让自己更有精力",
            "selection_quote": "我选择每天晚饭后散步", "activity_quote": "每天晚饭后散步",
            "direction_quote": "让自己更有精力"},
        "plan_context": {"schedule_kind": "recurring", "schedule_quote": "每天晚饭后",
            "review_cadence": "每周日复盘", "difficulty": "每天", "resources": ["小区"]},
    }


@pytest.mark.asyncio
async def test_new_primary_goal_requires_source_evidence_but_router_steps_may_be_partial(goal_api):
    _, db, _ = goal_api
    latest_assistant = await seed_goal_dialogue(db)
    payload = primary_payload()
    invalid_payloads = [
        {**payload, "goal_proposal": None},
        {**payload, "goal_proposal": {**payload["goal_proposal"],
                                       "selection_quote": "可以考虑每天晚饭后散步"}},
        {**payload, "goal_proposal": {**payload["goal_proposal"],
                                       "direction_quote": "并不在用户原话里"}},
    ]

    for invalid in invalid_payloads:
        assert await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a", data=invalid,
            completed_steps=[], assistant_message_id=latest_assistant) is None
    assert await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a", data=payload,
        completed_steps=["activity_selected"], assistant_message_id=latest_assistant - 2) is None

    created = await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a", data=payload,
        # The Router may omit values_or_intention_explored; source-bound
        # proposal evidence is the authority for the user's explicit choice.
        completed_steps=["activity_selected"], assistant_message_id=latest_assistant)
    assert created is not None
    await db.commit()
    detail = (await db.execute(select(schema.tables["pa_goal_details"]).where(
        schema.tables["pa_goal_details"].c.goal_id == created["goal_id"]))).mappings().one()
    context = (await db.execute(select(schema.tables["pa_plan_details"]).where(
        schema.tables["pa_plan_details"].c.plan_id == created["plan_id"]))).mappings().one()
    assert (detail["goal_kind"], detail["long_term_direction"], detail["source_message_id"]) == ("primary", "让自己更有精力", 102)
    assert (context["schedule_kind"], context["review_cadence"], context["difficulty"]) == ("recurring", "每周日复盘", "每天")


@pytest.mark.asyncio
async def test_omitted_goal_proposal_is_recovered_from_complete_plan_and_user_choice(goal_api):
    """The graph filters a null/omitted proposal before this service runs.

    A complete PA card plus an explicit user choice is enough to recover that
    omitted side-channel; the router's pa_card_completed bit is confirmation
    evidence and must not be a prerequisite for creating the draft goal.
    """
    _, db, _ = goal_api
    latest_assistant = await seed_goal_dialogue(db, conversation_id=2, first_id=300)
    payload = primary_payload()
    payload.pop("goal_proposal")
    payload.update({
        "frequency_rule": {"schema_version": 1, "text": "每天"},
        "potential_barriers": ["容易忘记"],
        "barrier_coping_plan": [{"barrier": "容易忘记", "plan": "晚饭后设置提醒"}],
    })
    created = await create_goal_from_agent_dialogue(
        db, session_id="new-chat-a", user_id="a", data=payload,
        completed_steps=["activity_selected"], assistant_message_id=latest_assistant,
    )
    assert created is not None
    await db.commit()
    goal = (await db.execute(select(schema.tables["pa_goals"]).where(
        schema.tables["pa_goals"].c.id == created["goal_id"]))).mappings().one()
    assert goal["title"] == "每天晚饭后散步"


@pytest.mark.asyncio
async def test_unusable_extractor_activity_is_recovered_when_choice_is_source_bound(goal_api):
    """A non-empty but invented activity quote must not strand a valid card."""
    _, db, _ = goal_api
    latest_assistant = await seed_goal_dialogue(db)
    payload = primary_payload()
    payload.update({
        "frequency_rule": {"schema_version": 1, "text": "每天"},
        "potential_barriers": ["贪睡", "周日晚间容易睡过头"],
        "barrier_coping_plan": [{"barrier": "贪睡", "plan": "设置闹钟"}],
        "goal_proposal": {**payload["goal_proposal"],
            "selection_quote": "我选择每天晚饭后散步",
            "activity_quote": "和朋友一起散步"},
    })
    created = await create_goal_from_agent_dialogue(
        db, session_id="new-chat-a", user_id="a", data=payload,
        completed_steps=["activity_selected"], assistant_message_id=latest_assistant,
    )
    assert created is not None
    await db.commit()
    goal = (await db.execute(select(schema.tables["pa_goals"]).where(
        schema.tables["pa_goals"].c.id == created["goal_id"]))).mappings().one()
    assert goal["title"] == "每天晚饭后散步"


@pytest.mark.asyncio
async def test_secondary_goal_is_independent_and_assistant_only_or_stale_evidence_is_rejected(goal_api):
    _, db, _ = goal_api
    latest_assistant = await seed_goal_dialogue(db)
    payload = primary_payload()
    payload["goal_proposal"] = {"goal_kind": "secondary", "long_term_direction": "不应保存",
        "selection_quote": "我选择每天晚饭后散步", "activity_quote": "每天晚饭后散步", "direction_quote": None}
    created = await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a", data=payload,
        completed_steps=["activity_selected", "values_or_intention_explored"], assistant_message_id=latest_assistant)
    assert created is not None
    await db.commit()
    detail = (await db.execute(select(schema.tables["pa_goal_details"]).where(
        schema.tables["pa_goal_details"].c.goal_id == created["goal_id"]))).mappings().one()
    assert detail["goal_kind"] == "secondary" and detail["long_term_direction"] is None
    assert (await db.execute(select(schema.tables["pa_goal_details"].c.goal_id).where(
        schema.tables["pa_goal_details"].c.goal_kind == "primary"))).scalars().all() == []


@pytest.mark.asyncio
async def test_verified_activity_working_value_does_not_break_goal_detail_refresh(goal_api):
    _, db, _ = goal_api
    latest = await seed_goal_dialogue(db)
    payload = primary_payload()
    created = await create_goal_from_agent_dialogue(db, session_id='new-chat-a', user_id='a',
        data=payload, completed_steps=[], assistant_message_id=latest)
    messages = list((await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == 2).order_by(ConversationMessage.position))).scalars())
    evidence = proposal_evidence(payload['goal_proposal'], messages, payload['target_activity_content'])
    assert evidence['source_activity'] == '每天晚饭后散步'
    # This second save previously failed with "Unconsumed column names:
    # source_activity", rolling back the complete M2 extraction transaction.
    await save_goal_details(db, created['goal_id'], evidence)
    await db.commit()
    details = schema.tables['pa_goal_details']
    row = (await db.execute(select(details).where(details.c.goal_id == created['goal_id']))).mappings().one()
    assert row['source_message_id'] == 102
    assert row['long_term_direction'] == '让自己更有精力'


@pytest.mark.asyncio
async def test_capture_activities_uses_only_the_latest_user_message_and_is_idempotent(goal_api):
    _, db, _ = goal_api
    await db.execute(insert(ConversationMessage), [
        {"id": 200, "conversation_id": 1, "position": 0, "role": "user", "content": "昨天游泳了。"},
        {"id": 201, "conversation_id": 1, "position": 1, "role": "assistant", "content": "谢谢你的补充。"},
        {"id": 202, "conversation_id": 1, "position": 2, "role": "user", "content": "今天没站桩，但散步了，感觉轻松。游泳可能不错。"},
        {"id": 203, "conversation_id": 1, "position": 3, "role": "assistant", "content": "我会记录今天实际发生的活动。"},
    ])
    await db.commit()
    conversation = (await db.execute(select(Conversation).where(Conversation.id == 1))).scalar_one()
    _, state = await runtime_for(db, "chat-a")
    messages = await evidence_messages(db, conversation.id, "a")
    raw = [
        {"event_kind": "not_performed", "activity_content": "站桩", "source_quote": "今天没站桩，但散步了，感觉轻松", "occurred_at_text": "今天"},
        {"event_kind": "performed", "activity_content": "散步", "source_quote": "今天没站桩，但散步了，感觉轻松", "effect": "感觉轻松"},
        {"event_kind": "idea", "activity_content": "游泳", "source_quote": "游泳可能不错"},
        {"event_kind": "performed", "activity_content": "游泳", "source_quote": "昨天游泳了"},
        {"event_kind": "performed", "activity_content": "跑步", "source_quote": "不存在的引用"},
    ]
    await capture_activities(db, user_id="a", conversation=conversation, state=state, raw=raw, messages=messages)
    await db.commit()
    activities = schema.tables["pa_activity_events"]
    first = (await db.execute(select(activities).where(activities.c.source_message_id == 202))).mappings().all()
    assert {(row["activity_content"], row["event_kind"], row["goal_id"]) for row in first} == {
        ("站桩", "not_performed", None), ("散步", "performed", None), ("游泳", "idea", None)}
    assert (await db.execute(select(schema.tables["pa_goals"].c.id).where(
        schema.tables["pa_goals"].c.user_id == "a"))).scalars().all() == ["g1", "g2"]
    await capture_activities(db, user_id="a", conversation=conversation, state=state, raw=raw, messages=messages)
    await db.commit()
    assert len((await db.execute(select(activities).where(activities.c.source_message_id == 202))).mappings().all()) == 3


@pytest.mark.asyncio
async def test_overview_has_safe_fallbacks_and_keeps_schedule_and_review_cadence_independent(goal_api):
    client, db, _ = goal_api
    plans, goals = schema.tables["module_two_record"], schema.tables["pa_goals"]
    await db.execute(insert(plans), {"id": "p-context", "goal_id": "g1", "version_no": 1,
        "timezone": "Asia/Shanghai", "record_status": "confirmed", "confirmation_status": "confirmed",
        "confirmation_message_id": 1, "activity_content": "晚饭后散步", "schedule_text": "每周三次"})
    await db.execute(update(goals).where(goals.c.id == "g1").values(current_plan_record_id="p-context"))
    await db.execute(insert(schema.tables["pa_goal_details"]), {"goal_id": "g1", "goal_kind": "primary",
        "long_term_direction": "保持精力", "source_conversation_id": 1, "source_message_id": 1,
        "evidence": {"selection_quote": "散步", "activity_quote": "散步", "direction_quote": "保持精力"}})
    await db.execute(insert(schema.tables["pa_plan_details"]), {"plan_id": "p-context", "schedule_kind": "recurring",
        "review_cadence": "每两周复盘"})
    await db.commit()
    payload = (await client.get("/api/program/goals/overview")).json()
    rows = {goal["id"]: goal for goal in payload["goals"]}
    assert rows["g1"]["goal_kind"] == "primary" and rows["g1"]["long_term_direction"] == "保持精力"
    assert rows["g1"]["plan"]["schedule_kind"] == "recurring"
    assert rows["g1"]["plan"]["review_cadence"] == "每两周复盘"
    assert rows["g2"]["goal_kind"] == "unclassified" and rows["g2"]["long_term_direction"] is None
