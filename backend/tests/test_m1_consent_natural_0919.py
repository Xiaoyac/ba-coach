"""Natural goal invitations still bind to verified, owned dialogue evidence."""
import pytest
from sqlalchemy import insert, select, update, func

from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.m1_contract import consent_is_current, normalize, snapshot
from app.v2_workflow import record_values, record_steps, runtime_for
from test_goal_overview import goal_api
from test_m1_contract_0914 import _raw, _data, _turns

INVITATION = "那我最后确认一件小事：你愿意试试用这种方式，接下来一起设定一个具体、可操作的小目标吗？如果愿意，我们就往下走走看；有顾虑也可以直接说。"
REAL_INVITATION = "既然这个视角和你的体会基本一致，接下来我们可以试着把这种方法落到实处——从你能调整的行动入手，一起看看可以设立什么样的小目标。你愿意试试看吗？"


@pytest.mark.parametrize("question", [INVITATION, "我们接下来一起制定一个小目标，可以吗？",
    "你愿意和我一起讨论下一步的小目标吗？", "你愿意进入接下来的目标设定环节吗？",
    "愿不愿意试试这种从行动入手的方式，接下来我们一起商量一个可以尝试的小目标？"])
@pytest.mark.parametrize("answer", ["我愿意", "好的", "可以"])
def test_natural_single_invitation(question, answer):
    assert consent_is_current([("assistant", question), ("user", answer)], 1)


def test_natural_invitation_with_set_li_wording_is_scoped():
    assert consent_is_current([("assistant", REAL_INVITATION), ("user", "愿意")], 1)


def test_recorded_conversation_invitation_is_scoped():
    assistant = ("既然这个视角和你的体会基本一致，接下来我们可以试着把这种方法落到实处——"
                 "从你能调整的行动入手，一起看看可以设立什么样的小目标。你愿意试试看吗？")
    assert consent_is_current([("assistant", assistant), ("user", "愿意")], 1)


def test_education_before_goal_invitation_does_not_veto_consent():
    assistant = ("补一点原理：BA 更希望人按计划行动，而不是等情绪好了再动。"
                 "\n\n不用现在就确定做什么。我想确认的是：你愿意开始和我一起讨论一个小目标吗？"
                 "愿意的话，我们再一起看从哪儿下手。")
    assert consent_is_current([("assistant", assistant), ("user", "嗯嗯好的可以")], 1)


@pytest.mark.parametrize("question", ["这个总结符合吗？", "你愿意听我解释如何设定目标吗？",
    "你愿意了解目标设定的原理吗？", "你愿意一起设定目标还是先聊别的？",
    "你愿意一起制定目标吗？还要继续聊吗？", "刚才我们聊过一起设定目标。你愿意了解BA吗？",
    "比如‘你愿意一起设定目标吗？’只是一个例子。", "你愿意按这个计划每天执行目标吗？",
    "你不愿意一起设定目标吗？"])
def test_unrelated_ambiguous_or_negative_invitation_still_blocks(question):
    assert not consent_is_current([("assistant", question), ("user", "我愿意")], 1)


@pytest.mark.parametrize("answer", ["不愿意", "愿意吗？", "我愿意，但是先等等", "我再想想", "她说我愿意"])
def test_uncertain_answers_are_not_consent(answer):
    assert not consent_is_current([("assistant", INVITATION), ("user", answer)], 1)


def consent_fixture():
    turns = _turns()[:9] + [("user", "我理解了，没有其他问题。"),
                           ("assistant", INVITATION), ("user", "我愿意"),
                           ("assistant", "谢谢你告诉我你的意愿。")]
    raw = _raw()
    raw.update(understanding_quote={"turn": 9}, consent_quote={"turn": 11})
    return turns, raw


def test_consent_alone_cannot_supply_missing_education():
    turns, raw = consent_fixture()
    raw["education_quotes"][1] = None
    contract = normalize(raw, _data(), turns, "synthetic")
    assert contract["goal_consent_expressed"]
    assert "ba_understanding" in contract["missing_fields"]
    assert not contract["milestones"]["m1_milestone_3"]


@pytest.mark.parametrize("case", ["valid", "later_neutral", "withdrawal", "wrong_hash", "missing_education"])
async def test_actual_database_transition_and_next_turn_module(goal_api, case):
    _, db, _ = goal_api
    turns, raw = consent_fixture()
    if case in {"later_neutral", "withdrawal"}:
        turns += [("user", "先不要开始目标设定" if case == "withdrawal" else "我喜欢看电影"),
                  ("assistant", "收到你说的内容。")]
    if case == "missing_education":
        raw["education_quotes"][1] = None
    await db.execute(insert(ConversationMessage), [dict(id=200+i, conversation_id=1, position=i,
        role=role, content=content) for i, (role, content) in enumerate(turns)])
    m1 = schema.tables["user_module_one_state"]
    await db.execute(update(m1).where(m1.c.user_id == "a").values(status="in_progress", completed_steps=[]))
    last_id = 200 + len(turns) - 1
    data = snapshot({"m1_contract": raw}, _data(), turns, "chat-a")
    data["m1_contract"]["assistant_message_id"] = last_id
    if case == "wrong_hash":
        data["m1_contract"]["transcript_hash"] = "wrong"
    await db.execute(insert(schema.tables["module_one_record"]), dict(id="natural-m1", user_id="a",
        version_no=1, **record_values("module_1", data)))
    goal_count = (await db.execute(select(func.count()).select_from(schema.tables["pa_goals"]))).scalar_one()
    target, cycle = await record_steps(db, session_id="chat-a", user_id="a", module="module_1",
        requested_target="module_2", steps=[], assistant_message_id=last_id)
    await db.commit()
    expected = "module_2" if case in {"valid", "later_neutral"} else "module_1"
    assert target == expected
    _, runtime = await runtime_for(db, "chat-a")
    assert runtime["current_module"] == expected  # not merely a Router suggestion
    assert cycle is None  # agreeing to discuss is not creating a goal
    assert (await db.execute(select(func.count()).select_from(schema.tables["pa_goals"]))).scalar_one() == goal_count
