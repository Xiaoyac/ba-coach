"""Real M1 blocking shape, verified against an isolated transactional database."""
from copy import deepcopy
import pytest
from sqlalchemy import insert, select, update, func
from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.m1_contract import normalize, snapshot, reconcile_router_completion, consent_is_current
from app.router_agent import RouterDecision, format_routing_reasoning, extract_pa_card
from app.v2_workflow import record_values, record_steps, runtime_for
from app.workflow_contract import MODULE_STEP_KEYS
from test_goal_overview import goal_api
from test_m1_contract_0914 import _turns, _raw, _data

INVITATION = ('接下来我想和你一起，把“定个读书目标、完成再睡”这类想法，慢慢落成具体可执行的小目标。'
              '\n你愿意试试这个方法，进入目标设定的部分吗？')


def test_pa_card_extractor_accepts_core_card_heading():
    card = extract_pa_card('前面的话\n当前核心 PA 目标\n• 活动内容：晚饭后散步')
    assert card and card.startswith('当前核心 PA 目标')


def scenario(case="valid"):
    turns = _turns()[:9] + [
        ("user", "贴合"),
        ("assistant", "所以我们的做法是尝试、观察、调整，而不是要求你靠意志力硬撑。到这里有还没说清或让你有顾虑的地方吗？"),
        ("user", "没有"), ("assistant", INVITATION), ("user", "愿意"), ("assistant", "收到你的意愿。")]
    raw = _raw()
    raw.update(understanding_quote={"turn": 9}, consent_quote={"turn": 13})
    raw["education_quotes"][-1] = {"turn": 10, "quote": "所以我们的做法是尝试、观察、调整，而不是要求你靠意志力硬撑。"}
    if case == "unrelated_no":
        turns[10] = ("assistant", "所以我们的做法是尝试、观察、调整，而不是要求你靠意志力硬撑。你有朋友吗？")
    if case == "quoted_no":
        turns[11] = ("user", "她说没有")
    if case == "missing_education":
        raw["education_quotes"][1] = None
    if case == "unresolved":
        raw["core_questions_resolved"] = False
    if case == "declined":
        turns[13] = ("user", "不愿意")
    if case == "education_invitation":
        turns[12] = ("assistant", "你愿意听我解释如何设定目标吗？")
    if case in {"later_withdrawal", "later_question", "later_neutral"}:
        body = {"later_withdrawal": "我改主意了，先不要开始目标设定。",
                "later_question": "我还是不理解，行动为什么有用？",
                "later_neutral": "好的，我喜欢玩鬼抓人"}[case]
        turns += [("user", body), ("assistant", "谢谢你告诉我。")]
    return turns, raw


def test_real_invitation_with_quoted_prior_plan_is_not_a_quoted_invitation():
    assert consent_is_current([("assistant", INVITATION), ("user", "愿意")], 1)
    for question in ['比如“你愿意开始目标设定吗？”只是例子。',
                     '她问“你愿意开始目标设定吗？”你听清了吗？',
                     '你愿意开始目标设定吗？还是继续讨论？']:
        assert not consent_is_current([("assistant", question), ("user", "愿意")], 1)


@pytest.mark.parametrize("case", ["valid", "later_neutral", "unrelated_no", "quoted_no", "missing_education",
    "unresolved", "declined", "education_invitation", "later_withdrawal", "later_question",
    "wrong_hash", "stale", "wrong_session", "router_stay", "missing_steps", "revoked"])
async def test_router_cannot_replace_missing_semantic_understanding_with_keywords(goal_api, case):
    _, db, _ = goal_api
    turns, raw = scenario(case)
    # Even a positive Router vote and a literal "没有" are not an extractor's
    # contextual understanding assessment. Do not recreate an understanding
    # passphrase in program code.
    raw["understanding_quote"] = None
    last_id = 400 + len(turns) - 1
    await db.execute(insert(ConversationMessage), [dict(id=400+i, conversation_id=1, position=i,
        role=role, content=body) for i, (role, body) in enumerate(turns)])
    await db.execute(update(schema.tables["user_module_one_state"]).values(status="in_progress", completed_steps=[]))
    data = snapshot({"m1_contract": raw}, _data(), turns, "chat-a")
    data["m1_contract"]["assistant_message_id"] = last_id
    assert "ba_understanding" in data["m1_contract"]["missing_fields"]  # original failure reproduced
    if case == "wrong_hash":
        data["m1_contract"]["transcript_hash"] = "wrong"
    if case == "stale":
        data["m1_contract"]["assistant_message_id"] -= 2
    if case == "wrong_session":
        data["m1_contract"]["session_id"] = "other-chat"
    await db.execute(insert(schema.tables["module_one_record"]), dict(id="router-review-m1", user_id="a",
        version_no=1, **record_values("module_1", data)))
    before = (await db.execute(select(func.count()).select_from(schema.tables["pa_goals"]))).scalar_one()
    diagnostics = {}
    target, cycle = await record_steps(db, session_id="chat-a", user_id="a", module="module_1",
        requested_target="module_1" if case == "router_stay" else "module_2",
        steps=[] if case == "missing_steps" else list(MODULE_STEP_KEYS["module_1"]),
        revoked_steps=["ba_education_completed"] if case == "revoked" else [],
        revocation_evidence="用户纠正证据" if case == "revoked" else None,
        assistant_message_id=last_id, diagnostics=diagnostics)
    await db.commit()
    expected = "module_1"
    assert target == expected and cycle is None
    _, runtime = await runtime_for(db, "chat-a")
    assert runtime["current_module"] == expected
    assert before == (await db.execute(select(func.count()).select_from(schema.tables["pa_goals"]))).scalar_one()
    record = (await db.execute(select(schema.tables["module_one_record"]))).mappings().one()
    assert record["record_status"] == "draft"
    if case != "router_stay":
        assert diagnostics["block_reasons"]
    logs = schema.tables["ai_decision_logs"]
    decision = (await db.execute(select(logs.c.decision_value).where(logs.c.decision_type == "step_completion"))).scalar_one()
    assert decision["applied_target"] == expected


def test_source_tampering_and_pure_function_do_not_mutate_original():
    turns, raw = scenario()
    raw["consent_quote"] = None
    contract = normalize(raw, _data(), turns, "chat-a")
    contract["assistant_message_id"] = 99
    original = deepcopy(contract)
    reviewed = reconcile_router_completion(contract, turns, session_id="chat-a", assistant_message_id=99)
    assert reviewed and reviewed["missing_fields"] == []
    assert contract == original
    contract["evidence"]["education_0"]["turn"] = 0  # a user cannot supply assistant education
    assert reconcile_router_completion(contract, turns, session_id="chat-a", assistant_message_id=99) is None


def test_debug_view_explains_veto_and_router_adoption():
    decision = RouterDecision("module_2", "", "router", list(MODULE_STEP_KEYS["module_1"]), {})
    denied = format_routing_reasoning(decision, "module_1", "module_1",
        diagnostics={"block_reasons": ["最新一轮证据尚未成功刷新"]})
    assert "本轮未跳转原因：最新一轮证据尚未成功刷新" in denied
    accepted = format_routing_reasoning(decision, "module_1", "module_2",
        diagnostics={"policy": "router_evidence_reconciled", "block_reasons": []})
    assert "已采纳 Router" in accepted and "module_1 → module_2" in accepted
