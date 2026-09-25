"""0924 manual: contextual education, optional material and low disclosure."""
import pytest

from app.clinical_extraction import extract_module_record_detailed
from app.m1_contract import merge_verified_evidence, normalize
from test_m1_contract_0914 import _data, _raw, _turns


@pytest.mark.parametrize("methods", [None, [], ["列清单"]])
def test_three_core_topics_complete_without_optional_topics_or_methods(methods):
    raw = _raw()
    raw["education_quotes"] = raw["education_quotes"][:3]
    raw["methods_quote"] = None
    result = normalize(raw, _data(attempted_relief_methods=methods), _turns(), "manual")
    assert result["missing_fields"] == []
    assert result["milestones"]["m1_milestone_3"]


@pytest.mark.parametrize("reference", [{"turn": 0}, {"turn": True}, {"turn": 999}])
def test_core_education_still_requires_real_assistant_sources(reference):
    raw = _raw()
    raw["education_quotes"] = [reference, {"turn": 5}, {"turn": 6}]
    result = normalize(raw, _data(), _turns(), "manual")
    assert not result["education_evidence_complete"]
    assert "ba_understanding" in result["missing_fields"]


def test_contextual_understanding_needs_no_passphrase_or_definition_repetition():
    turns = _turns()
    turns[8] = ("assistant", "刚才关于行动与反馈的解释，你觉得有哪些地方还需要说明？")
    turns[9] = ("user", "这和我的经历能对上，暂时没有要补充的。我愿意进入目标设定。")
    raw = _raw()
    raw["education_quotes"] = [{"turn": i} for i in (4, 5, 6)]
    raw["understanding_quote"] = {"turn": 9}
    result = normalize(raw, _data(), turns, "manual")
    assert result["understanding_verified"]
    assert result["milestones"]["m1_milestone_3"]


def test_no_user_understanding_source_cannot_be_replaced_by_silence():
    raw = _raw()
    raw["understanding_quote"] = None
    assert not normalize(raw, _data(), _turns(), "manual")["understanding_verified"]


def low_disclosure_fixture():
    turns = [
        ("user", "个人经历我暂时不想谈。"),
        ("assistant", "可以。缺少个人经历时，我只能解释一般原理，不能结合你的情况分析。"),
        ("user", "知道，先了解一般原理就好。"),
        ("assistant", "情绪会影响行动，行动与环境反馈也会影响状态；减少活动可能减少愉悦和成就反馈，让困扰维持；行动可以带来新反馈，为状态改变创造机会。"),
        ("user", "这样解释我能跟上，我愿意进入目标设定。"),
    ]
    raw = {"path": "low_disclosure", "refusal_quote": {"turn": 0},
           "limitation_explained_quote": {"turn": 1},
           "limitation_acknowledged_quote": {"turn": 2},
           "education_quotes": [{"turn": 3}] * 3,
           "understanding_quote": {"turn": 4}, "consent_quote": {"turn": 4},
           "core_questions_resolved": True}
    return turns, raw


def test_low_disclosure_completes_without_personal_facts_or_summary():
    turns, raw = low_disclosure_fixture()
    result = normalize(raw, {}, turns, "manual")
    assert result["missing_fields"] == []
    assert all(result[key] for key in
               ("disclosure_declined", "limitation_explained", "limitation_acknowledged"))
    assert not any(result["verified_facts"].values())


@pytest.mark.parametrize("field,value", [
    ("limitation_explained_quote", None),
    ("limitation_explained_quote", {"turn": 2}),
    ("limitation_acknowledged_quote", None),
    ("limitation_acknowledged_quote", {"turn": 0}),
    ("limitation_acknowledged_quote", {"turn": 1}),
])
def test_low_disclosure_requires_explained_and_acknowledged_limits(field, value):
    turns, raw = low_disclosure_fixture()
    raw[field] = value
    result = normalize(raw, {}, turns, "manual")
    assert result["disclosure_declined"]
    assert not result["milestones"]["m1_milestone_3"]
    assert "m1_milestone_2" in result["missing_fields"]


def test_latest_semantic_assessment_controls_reuse_not_question_keywords():
    original_turns = _turns()
    previous = normalize(_raw(), _data(), original_turns, "manual")
    turns = original_turns + [("user", "没有疑问，也没有顾虑。")]
    raw = _raw()
    raw["education_quotes"][1] = None
    raw["understanding_quote"] = raw["consent_quote"] = None
    candidate = normalize(raw, _data(), turns, "manual")
    result = merge_verified_evidence(previous, candidate, turns, session_id="manual")
    assert result["missing_fields"] == []
    raw["core_questions_resolved"] = False
    candidate = normalize(raw, _data(), turns, "manual")
    result = merge_verified_evidence(previous, candidate, turns, session_id="manual")
    assert not result["understanding_verified"]
    assert "ba_understanding" in result["missing_fields"]


async def test_extractor_receives_field_types_and_new_evidence_definition(provider):
    await extract_module_record_detailed(provider, module="module_1", transcript="[]", max_tokens=1000)
    prompt = provider.route_systems[-1]
    assert "integer 0/1/2 或 null" in prompt
    assert "最长 64 字符" in prompt
    assert "limitation_acknowledged_quote" in prompt
    assert "不是独立完成门槛" in prompt
    assert "恰好5个位置" not in prompt
    assert "各取不同的精确原文" not in prompt
