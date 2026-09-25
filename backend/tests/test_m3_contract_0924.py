import copy
from types import SimpleNamespace

import pytest

from app.m3_contract import missing_fields, normalize
from app.workflow_readiness import evaluate_readiness


def message(identifier, role, content):
    return SimpleNamespace(id=identifier, position=identifier, role=role, content=content, conversation_id=1)


MESSAGES = [
    message(1, "assistant", "每天记录整体感受，活动记录可以补充。可以每天在记录今日填写一次。"),
    message(2, "user", "我理解了，就每天记一次。"),
    message(3, "assistant", "没完成、受阻或忘记了也可以在聊天里告诉我。"),
    message(4, "user", "活动记录我不想填，但每天整体感受会继续记。"),
    message(5, "assistant", "尊重你的选择。少了活动记录，复盘时能参考的具体信息会少一些。没完成、受阻或忘记了也可以在聊天里告诉我。"),
]


def proposal(status="accepted"):
    return {
        "recording_status":status, "negotiated_record_plan":"伪造或过时的字段值不应成为最终计划",
        "recording_evidence":{
            "requirement":{"message_id":1, "quote":"每天记录整体感受，活动记录可以补充。"},
            "decision":{"message_id":2 if status == "accepted" else 4,
                "quote":"我理解了，就每天记一次。" if status == "accepted" else "活动记录我不想填，但每天整体感受会继续记。",
                "status":status, "scope":"current_arrangement" if status == "accepted" else "activity_record"},
            "plan":{"message_id":1, "quote":"可以每天在记录今日填写一次。"},
            "feedback":{"message_id":3, "quote":"没完成、受阻或忘记了也可以在聊天里告诉我。"},
            "limitations":{"message_id":5, "quote":"少了活动记录，复盘时能参考的具体信息会少一些。"},
        },
    }


def normalized(raw, messages=MESSAGES, **overrides):
    return normalize(raw, messages, session_id="chat", cycle_id="cycle",
                     assistant_message_id=overrides.get("assistant_message_id", messages[-1].id))


def test_natural_acceptance_needs_no_second_formal_promise():
    values = normalized(proposal(), MESSAGES[:3])
    assert values["recording_status"] == "accepted"
    assert values["negotiated_record_plan"]["text"] == "可以每天在记录今日填写一次。"
    assert missing_fields(values, session_id="chat", cycle_id="cycle") == []
    assert evaluate_readiness("module_3", values)["ready"]


def test_current_user_acceptance_can_commit_before_generating_reply():
    messages = [message(1, "assistant", MESSAGES[0].content + MESSAGES[2].content), MESSAGES[1]]
    raw = proposal()
    raw["recording_evidence"]["feedback"]["message_id"] = 1
    values = normalize(raw, messages, session_id="chat", cycle_id="cycle", user_message_id=2)
    assert missing_fields(values) == []
    assert values["recording_evidence"]["user_message_id"] == 2
    assert values["recording_evidence"]["assistant_message_id"] is None


def test_user_boundary_must_refer_to_the_latest_real_user_message():
    values = normalize(proposal(), MESSAGES[:3], session_id="chat", cycle_id="cycle", user_message_id=2)
    assert values["recording_status"] == "unknown"


def test_declined_does_not_require_or_fabricate_recording_plan():
    values = normalized(proposal("declined"))
    assert values["recording_status"] == "declined" and values["negotiated_record_plan"] is None
    assert missing_fields(values) == [] and evaluate_readiness("module_3", values)["ready"]
    assert values["recording_evidence"]["evidence"]["decision"]["scope"] == "activity_record"
    assert "每天整体感受会继续记" in values["recording_evidence"]["evidence"]["decision"]["quote"]


@pytest.mark.parametrize("count", [0, 1, 2, 99])
def test_number_of_explanations_is_never_a_business_gate(count):
    raw = proposal("declined")
    raw["explanation_count"] = count
    assert evaluate_readiness("module_3", normalized(raw))["ready"]


def test_refusing_pa_does_not_set_recording_decline():
    raw = proposal("declined")
    raw["recording_evidence"]["decision"].update(quote="今天我不想散步了。", scope="pa_execution")
    messages = [*MESSAGES[:3], message(4, "user", "今天我不想散步了。"), MESSAGES[4]]
    values = normalized(raw, messages)
    assert values["recording_status"] == "unknown"


def test_decline_keeps_limitations_and_feedback_as_actual_source_requirements():
    raw = proposal("declined")
    raw["recording_evidence"]["limitations"] = None
    values = normalized(raw)
    assert "recording_limitations_evidence" in missing_fields(values)
    assert "negotiated_record_plan" not in missing_fields(values)
    assert not evaluate_readiness("module_3", values)["ready"]


@pytest.mark.parametrize("change", ["assistant_decision", "invented_quote", "pa_refusal", "unknown", "mismatched_status"])
def test_unverified_or_nonrecording_decision_cannot_become_declined(change):
    raw = proposal("declined")
    decision = raw["recording_evidence"]["decision"]
    if change == "assistant_decision":
        decision.update(message_id=5, quote="尊重你的选择。")
    elif change == "invented_quote":
        decision["quote"] = "这是用户没说过的话"
    elif change == "pa_refusal":
        decision["scope"] = "pa_execution"
    elif change == "unknown":
        raw["recording_status"] = "unknown"
    else:
        decision["status"] = "accepted"
    values = normalized(raw)
    assert values["recording_status"] == "unknown" and not evaluate_readiness("module_3", values)["ready"]


def test_attitude_and_explanation_count_cannot_grant_decision():
    values = normalized({"user_acceptance_level":2, "has_contract_reached":True,
                         "explanation_count":200, "recording_status":"accepted"})
    assert values["acceptance_status"] == "2" and values["recording_status"] == "unknown"
    assert "explanation_count" not in values


def test_stale_boundary_and_other_cycle_cannot_confirm():
    values = normalized(proposal(), MESSAGES[:3], assistant_message_id=1)
    assert values["recording_status"] == "unknown"
    values = normalized(proposal(), MESSAGES[:3])
    assert missing_fields(values, cycle_id="different")
    assert missing_fields(values, session_id="different")


def test_new_plan_after_previous_acceptance_needs_new_decision():
    raw = proposal()
    raw["recording_evidence"]["plan"] = {"message_id":3, "quote":"每小时填写一次。"}
    values = normalized(raw, [*MESSAGES[:2], message(3, "assistant", "每小时填写一次。")])
    assert values["recording_status"] == "unknown"


def test_stored_plan_edit_does_not_reuse_old_confirmation():
    values = normalized(proposal(), MESSAGES[:3])
    changed = copy.deepcopy(values)
    changed["negotiated_record_plan"]["text"] = "每小时填写一次"
    assert "recording_plan_evidence" in missing_fields(changed)


def test_omitted_decision_does_not_erase_sourced_facts_or_reuse_consent():
    existing = normalized(proposal(), MESSAGES[:3])
    values = normalize({}, MESSAGES[:3], session_id="chat", cycle_id="cycle",
                       assistant_message_id=3, existing=existing)
    assert values["record_requirement"] == existing["record_requirement"]
    assert values["feedback_mechanism"] == existing["feedback_mechanism"]
    assert values["recording_status"] == "unknown" and not evaluate_readiness("module_3", values)["ready"]


def test_repeated_short_quote_requires_a_unique_source():
    raw = proposal()
    raw["recording_evidence"]["decision"] = {"quote":"好的", "status":"accepted", "scope":"current_arrangement"}
    messages = [MESSAGES[0], message(2, "user", "好的"), MESSAGES[2],
                message(4, "user", "好的"), MESSAGES[4]]
    assert normalized(raw, messages)["recording_status"] == "unknown"
