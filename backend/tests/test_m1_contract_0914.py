import pytest
from app.m1_contract import VERSION, contract_for, missing_m1_fields, normalize


def _data(**overrides):
    data = {
        "chief_complaint": "最近工作后很难开始学习",
        "trigger_situation": "昨晚下班回家看到学习资料",
        "coping_behavior": "我躺下刷手机，没有开始学习",
        "coping_consequence": "后来更焦虑，也错过了学习时间",
        "abc_event": {"trigger": "昨晚下班回家看到学习资料", "feeling": "焦虑", "behavior": "躺下刷手机", "consequence": "更焦虑"},
        "ai_depression_cycle_summary": "当学习前躺下刷手机，可能暂时缓解压力，却减少了学习后的掌控感",
        "user_approval_level": 2,
        "attempted_relief_methods": ["先列小清单"],
    }
    data.update(overrides)
    return data


def _turns():
    return [
        ("user", "昨晚下班回家看到学习资料，我很焦虑，于是躺下刷手机，没有开始学习，后来更焦虑，也错过了学习时间。"),
        ("assistant", "你昨晚看到资料后焦虑，躺下刷手机而没有学习，之后更焦虑，也错过了时间。当学习前躺下刷手机，可能暂时缓解压力，却减少了学习后的掌控感"),
        ("user", "嗯，这个总结基本符合。"),
        ("user", "我试过先列小清单。"),
        ("assistant", "行动和情绪、精力会相互影响。"),
        ("assistant", "活动和反馈减少，有时会让困扰更难变化。"),
        ("assistant", "从可调整的行动入手，可能获得新的反馈。"),
        ("assistant", "行为激活不保证做了就开心。"),
        ("assistant", "可以尝试、观察反馈，再调整。"),
        ("user", "我理解了，这不是做了就立刻开心，而是观察反馈。我愿意进入目标设定。"),
    ]


def _raw(path="personalized"):
    return {"path": path, "fact_quotes": {"trigger": "昨晚下班回家看到学习资料", "feeling": "焦虑", "behavior": "躺下刷手机，没有开始学习", "consequence": "后来更焦虑，也错过了学习时间"}, "summary_quote": "当学习前躺下刷手机，可能暂时缓解压力，却减少了学习后的掌控感", "approval_quote": "嗯，这个总结基本符合。", "methods_quote": "我试过先列小清单。", "education_quotes": ["行动和情绪、精力会相互影响。", "活动和反馈减少，有时会让困扰更难变化。", "从可调整的行动入手，可能获得新的反馈。", "行为激活不保证做了就开心。", "可以尝试、观察反馈，再调整。"], "understanding_quote": "我理解了，这不是做了就立刻开心，而是观察反馈。", "consent_quote": "我愿意进入目标设定。", "core_questions_resolved": True}


def test_personalized_requires_all_four_facts_and_allows_optional_nulls():
    c = normalize(_raw(), _data(distress_duration=None, distress_frequency=None, exception_positive_scene=None), _turns(), "s1")
    assert c["milestones"]["m1_milestone_2"] is True
    bad = normalize(_raw(), _data(abc_event={"trigger": None, "feeling": "焦虑", "behavior": "躺下刷手机", "consequence": "后来更焦虑"}), _turns(), "s1")
    assert "m1_milestone_1" in bad["missing_fields"]


def test_order_and_speaker_provenance_gate_completion():
    raw = _raw(); raw["approval_quote"] = "你昨晚看到资料后焦虑"
    c = normalize(raw, _data(), _turns(), "s1")
    assert c["milestones"]["m1_milestone_2"] is False
    raw = _raw(); raw["fact_quotes"]["feeling"] = "行动和情绪、精力会相互影响。"
    c = normalize(raw, _data(), _turns(), "s1")
    assert "m1_milestone_1" in c["missing_fields"]


def test_empty_raw_consent_before_education_and_unresolved_questions_fail_closed():
    assert normalize({}, _data(), _turns(), "s1")["milestones"]["m1_milestone_3"] is False
    raw = _raw(); raw["education_quotes"] = ["行动和情绪、精力会相互影响。"] * 5; raw["understanding_quote"] = "我愿意进入目标设定。"; raw["consent_quote"] = "我愿意进入目标设定。"
    assert normalize(raw, _data(), _turns(), "s1")["milestones"]["m1_milestone_3"] is False
    raw = _raw(); raw["core_questions_resolved"] = False
    assert "ba_understanding" in normalize(raw, _data(), _turns(), "s1")["missing_fields"]


def test_low_disclosure_requires_explicit_refusal_and_no_personal_summary():
    raw = _raw("low_disclosure"); raw["refusal_quote"] = "我不想分享个人经历。"
    turns = _turns() + [("user", "我不想分享个人经历。")]
    c = normalize(raw, _data(), turns, "s1")
    assert c["path"] == "low_disclosure" and c["milestones"]["m1_milestone_1"] is True
    raw["refusal_quote"] = None
    assert normalize(raw, _data(), turns, "s1")["milestones"]["m1_milestone_1"] is False


def test_methods_null_differs_from_empty_and_session_or_legacy_is_blocked():
    raw = _raw(); c = normalize(raw, _data(attempted_relief_methods=[]), _turns(), "s1")
    assert c["milestones"]["m1_milestone_2"] is True
    c["version"] = VERSION
    record = {"event_experience": {"_m1_contract": c}}
    assert contract_for(record)["version"] == VERSION
    assert missing_m1_fields(record, "other") == ["m1_evidence_refresh"]
    assert missing_m1_fields({"event_experience": {}}, "s1") == ["m1_evidence_refresh"]
    raw = _raw(); c = normalize(raw, _data(attempted_relief_methods=None), _turns(), "s1")
    assert "m1_milestone_2" in c["missing_fields"]


@pytest.mark.parametrize("key", ["trigger", "feeling", "behavior", "consequence"])
def test_each_fact_required(key):
    data = _data()
    data["abc_event"][key] = None
    assert not normalize(_raw(), data, _turns(), "s1")["milestones"]["m1_milestone_1"]


def test_corrected_summary_repeated_after_old_approval_requires_new_approval():
    turns = _turns() + [("assistant", _raw()["summary_quote"])]
    assert not normalize(_raw(), _data(), turns, "s1")["milestones"]["m1_milestone_2"]


def test_consent_before_education_is_not_current_understanding():
    turns = [_turns()[-1]] + _turns()[:-1]
    assert not normalize(_raw(), _data(), turns, "s1")["milestones"]["m1_milestone_3"]


def test_actual_low_disclosure_without_event_summary_or_methods():
    raw = _raw("low_disclosure")
    raw["refusal_quote"] = "我不想分享个人经历。"
    turns = [("user", raw["refusal_quote"])] + _turns()[4:]
    result = normalize(raw, {}, turns, "s1")
    assert result["missing_fields"] == []
    assert result["milestones"]["m1_milestone_3"]


@pytest.mark.parametrize("value", [None, {}, "yes", ["invented"]])
def test_malformed_education_evidence_blocks(value):
    raw = _raw()
    raw["education_quotes"] = value
    assert "ba_understanding" in normalize(raw, _data(), _turns(), "s1")["missing_fields"]


def test_explicit_rejection_is_recorded_as_zero_without_completing_summary():
    raw = _raw()
    raw["approval_quote"] = "这个总结不符合。"
    turns = _turns()
    turns[2] = ("user", "这个总结不符合。")
    result = normalize(raw, _data(user_approval_level=0), turns, "s1")
    assert result["user_approval_level"] == 0
    assert "m1_milestone_2" in result["missing_fields"]
