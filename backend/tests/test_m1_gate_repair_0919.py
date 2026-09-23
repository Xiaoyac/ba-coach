"""Regression tests for natural M1 invitations and actionable education gates."""

from app.m1_contract import (
    EDUCATION_TOPICS,
    consent_is_current,
    is_goal_discussion_invitation,
    normalize,
)


def test_short_consent_can_use_goal_scope_before_generic_question():
    invitation = "那我们接下来就从设定一个小目标开始……你愿意试试吗？"
    assert is_goal_discussion_invitation(invitation)
    assert consent_is_current([("assistant", invitation), ("user", "我愿意")], 1)


def test_short_consent_does_not_borrow_scope_from_execution_or_education():
    for invitation in (
        "你愿意按这个计划每天执行目标吗？",
        "你愿意听我解释如何设定目标吗？",
        "你愿意一起设定目标还是先聊别的？",
    ):
        assert not is_goal_discussion_invitation(invitation)
        assert not consent_is_current([("assistant", invitation), ("user", "我愿意")], 1)


def test_duplicate_education_is_reported_as_a_missing_topic():
    turns = [
        ("user", "昨晚回家很焦虑，刷手机没有学习，后来更焦虑。"),
        ("assistant", "当学习前刷手机，可能暂时缓解压力，却减少学习后的掌控感。"),
        ("user", "这个总结符合。"),
        ("user", "我试过先列一个小清单。"),
        ("assistant", "情绪、精力和行动会相互影响。"),
        ("assistant", "情绪、精力和行动会相互影响。"),
        ("assistant", "从可调整的行动入手，可能获得新的反馈。"),
        ("assistant", "行动不保证做了就立刻开心。"),
        ("assistant", "可以尝试、观察反馈，再调整。"),
        ("user", "我理解了，没有其他问题。"),
        ("assistant", "我们接下来一起设定一个小目标，可以吗？"),
        ("user", "可以。"),
    ]
    data = {
        "chief_complaint": "焦虑",
        "abc_event": {
            "trigger": "昨晚回家",
            "feeling": "焦虑",
            "behavior": "刷手机没有学习",
            "consequence": "后来更焦虑",
        },
        "trigger_situation": "昨晚回家",
        "coping_behavior": "刷手机没有学习",
        "coping_consequence": "后来更焦虑",
        "attempted_relief_methods": ["先列一个小清单"],
        "ai_depression_cycle_summary": "当学习前刷手机，可能暂时缓解压力，却减少学习后的掌控感。",
        "user_approval_level": 1,
    }
    raw = {
        "fact_quotes": {key: {"turn": 0} for key in ("trigger", "feeling", "behavior", "consequence")},
        "summary_quote": {"turn": 1, "quote": data["ai_depression_cycle_summary"]},
        "approval_quote": {"turn": 2},
        "methods_quote": {"turn": 3},
        "education_quotes": [
            {"turn": 4, "quote": turns[4][1]},
            {"turn": 5, "quote": turns[5][1]},
            {"turn": 6, "quote": turns[6][1]},
            {"turn": 7, "quote": turns[7][1]},
            {"turn": 8, "quote": turns[8][1]},
        ],
        "understanding_quote": {"turn": 9},
        "core_questions_resolved": True,
        "consent_quote": {"turn": 11},
    }
    result = normalize(raw, data, turns, "gate-test")
    assert not result["education_evidence_complete"]
    assert EDUCATION_TOPICS[1] in result["education_missing_topics"]
    assert {"field": "education_1", "reason": "duplicate_evidence", "same_as": "education_0"} in result["validation_issues"]
    assert "ba_understanding" in result["missing_fields"]
    # This is an internal evidence problem, not a reason to ask for consent again.
    assert "不要重问目标意愿" in __import__("app.m1_contract", fromlist=["dialogue_status"]).dialogue_status(result)["next_action"]


def test_newly_referenced_education_requires_a_new_understanding_quote():
    # If extraction points at a later education turn, the server must not
    # silently reuse an earlier understanding.  The next model turn can choose
    # the original evidence again when the later text was only a repetition.
    base = [
        ("assistant", "情绪、精力和行动会相互影响。"),
        ("assistant", "活动和反馈减少可能维持困扰。"),
        ("assistant", "可控行动可能带来新的反馈。"),
        ("assistant", "行动不保证做了就立刻开心。"),
        ("assistant", "可以尝试、观察反馈，再调整。"),
        ("user", "我理解了。"),
    ]
    turns = base + [("assistant", text) for _, text in base[:5]]
    raw = {"education_quotes": [{"turn": i + 6, "quote": turns[i + 6][1]} for i in range(5)],
           "understanding_quote": {"turn": 5}, "core_questions_resolved": True}
    result = normalize(raw, {"abc_event": {}}, turns, "repeat-test")
    assert "ba_understanding" in result["missing_fields"]
    assert {"field": "understanding", "reason": "before_new_education"} in result["validation_issues"]
