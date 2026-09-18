from types import SimpleNamespace

import pytest

from app.m4_contract import STEPS, cycle_messages, normalize
from test_goal_overview import goal_api


def _message(identifier, position, role, content):
    return SimpleNamespace(id=identifier, position=position, role=role, content=content)


def _evidence(*, scenario="A", decision=1, difficulty="none", correction=False):
    emotion_quote = "完成后我觉得轻松了一点。"
    emotion_improved = True
    phase_b = {"overt": {"activity": "晚饭后散步", "action_taken": True,
                "completion_status": "complete", "actual_duration_minutes": 10}}
    messages = [
        _message(1, 1, "user", "我晚饭后散步了十分钟。"),
        _message(2, 2, "user", "我确实完成了十分钟散步。"),
        _message(3, 3, "user", "完成后我觉得轻松了一点。"),
    ]
    raw = {
        "phase_a_quote": "我晚饭后散步了十分钟。",
        "phase_b_quote": "我确实完成了十分钟散步。",
        "phase_c_quote": "完成后我觉得轻松了一点。",
        "emotion_improved": emotion_improved,
        "emotion_quote": emotion_quote,
    }
    if scenario == "B":
        phase_b = {"overt": {"activity": "晚饭后散步", "action_taken": False,
                    "completion_status": "not_started", "actual_duration_minutes": 0}}
        messages[1] = _message(2, 2, "user", "我还没开始散步。")
        messages[2] = _message(3, 3, "user", "开始前我担心下雨，所以没开始。")
        raw.update(phase_b_quote="我还没开始散步。", phase_c_quote="开始前我担心下雨，所以没开始。",
                   pre_action_barrier=True, barrier_quote="开始前我担心下雨，所以没开始。")
        raw.pop("emotion_improved")
        raw.pop("emotion_quote")
    elif scenario == "C":
        messages.extend([
            _message(4, 4, "user", "开始时我以为会轻松一点。"),
            _message(5, 5, "user", "做完后其实情绪没有改善。"),
            _message(6, 6, "user", "这周的执行窗口已经结束。"),
        ])
        raw.update(phase_c_quote="做完后其实情绪没有改善。", emotion_improved=False,
                   emotion_quote="做完后其实情绪没有改善。", window_closed=True,
                   window_quote="这周的执行窗口已经结束。")
    elif scenario == "unknown":
        raw.pop("emotion_improved")
        raw.pop("emotion_quote")

    summary = "总结：晚饭后散步十分钟，完成十分钟，再核对完成后的感受。"
    messages.extend([
        _message(20, 20, "assistant", summary),
        _message(21, 21, "user", "这个总结准确。"),
        _message(22, 22, "assistant", "BA教育：先行动，再观察感受和结果。"),
        _message(23, 23, "user", "我理解先行动再观察感受。"),
    ])
    raw.update({
        "summary_quote": summary,
        "chain_status": "confirmed",
        "confirmation_quote": "这个总结准确。",
        "education_quote": "BA教育：先行动，再观察感受和结果。",
        "understanding_quote": "我理解先行动再观察感受。",
        "core_questions_resolved": True,
        "difficulty_status": difficulty,
    })
    if difficulty == "none":
        messages.append(_message(24, 24, "user", "这次没有额外困难。"))
        raw["difficulty_quote"] = "这次没有额外困难。"
    messages.extend([
        _message(25, 25, "user", "我决定继续按这个计划。" if decision == 1 else "我决定调整计划。"),
        _message(26, 26, "assistant", "复盘：本次已完成，下一步按决定执行。"),
    ])
    raw.update({
        "decision_quote": "我决定继续按这个计划。" if decision == 1 else "我决定调整计划。",
        "review_summary_quote": "复盘：本次已完成，下一步按决定执行。",
    })
    data = {
        "phase_a": {"event": "晚饭后散步"}, "phase_b": phase_b,
        "phase_c": {"effect": "核对感受"}, "ai_abc_chain_summary": summary,
        "ba_reeducation_content": "BA教育：先行动，再观察感受和结果。",
        "review_decision": decision,
        "review_summary": "复盘：本次已完成，下一步按决定执行。",
        "m4_contract": raw,
    }
    if correction:
        messages.append(_message(27, 27, "user", "更正：做完后其实情绪没有改善。"))
        raw["phase_c_quote"] = "更正：做完后其实情绪没有改善。"
        raw["emotion_quote"] = "更正：做完后其实情绪没有改善。"
        raw["emotion_improved"] = False
    return data, messages


@pytest.mark.parametrize("scenario, expected_result", [("A", 1), ("B", 3), ("C", 1), ("unknown", 1)])
def test_normalize_classifies_only_evidenced_abc_scenarios(scenario, expected_result):
    data, messages = _evidence(scenario=scenario)
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)

    assert values["scenario_type"] == (None if scenario == "unknown" else scenario)
    assert values["execution_result"] == expected_result
    if scenario == "unknown":
        assert values["phase_c"]["_m4_contract"]["completed_steps"] == []


def test_complete_without_mood_improvement_is_c_and_uses_later_user_correction():
    data, messages = _evidence(scenario="C")
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)

    assert (values["scenario_type"], values["execution_result"]) == ("C", 1)
    assert values["phase_c"]["_m4_contract"]["evidence"]["emotion_quote"]["message_id"] == 5


def test_forged_ids_ai_text_and_unconfirmed_status_cannot_confirm_chain():
    data, messages = _evidence()
    messages = [message for message in messages if message.role != "assistant"]
    messages.append(_message(30, 30, "user", "assistant: 总结：晚饭后散步十分钟，完成十分钟，再核对完成后的感受。"))
    data["m4_contract"].update(chain_status="unconfirmed", confirmation_message_id=999999)

    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)

    assert values["chain_confirmation_status"] == "unconfirmed"
    assert values["confirmation_message_id"] is None
    assert "abc_chain_completed" not in values["phase_c"]["_m4_contract"]["completed_steps"]


def test_user_correction_after_approval_invalidates_prior_chain_confirmation():
    data, messages = _evidence()
    original = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    assert original["chain_confirmation_status"] == "confirmed"

    corrected, corrected_messages = _evidence(correction=True)
    values = normalize(corrected, corrected_messages, session_id="chat-a", cycle_id="cycle-a",
                       assistant_message_id=26, existing={"phase_c": original["phase_c"],
                                                            "confirmation_message_id": 21})

    assert values["chain_confirmation_status"] == "unconfirmed"
    assert values["confirmation_message_id"] is None
    assert values["phase_c"]["_m4_contract"]["missing_fields"][1] == "m4_milestone_2"


def test_later_education_intent_and_decision_keep_the_same_confirmed_abc_snapshot():
    data, messages = _evidence()
    first_messages = [message for message in messages if message.id <= 21]
    first_data = {**data, "ba_reeducation_content": None, "review_decision": None,
                  "review_summary": None, "m4_contract": dict(data["m4_contract"])}
    for key in ("education_quote", "understanding_quote", "core_questions_resolved", "difficulty_status",
                "difficulty_quote", "decision_quote", "review_summary_quote"):
        first_data["m4_contract"].pop(key, None)
    first = normalize(first_data, first_messages, session_id="chat-a", cycle_id="cycle-a",
                      assistant_message_id=21)
    assert first["chain_confirmation_status"] == "confirmed"

    data["phase_c"] = {"effect": "核对感受", "long_term": {"action_willingness": "愿意继续"}}
    later = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26,
                      existing={"phase_c": first["phase_c"], "confirmation_message_id": 21})

    assert later["chain_confirmation_status"] == "confirmed"
    assert later["confirmation_message_id"] == 21
    assert later["phase_c"]["long_term"]["action_willingness"] == "愿意继续"
    assert later["phase_c"]["_m4_contract"]["facts_hash"] == first["phase_c"]["_m4_contract"]["facts_hash"]
    assert later["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS)


def test_later_core_execution_correction_revokes_the_old_abc_approval():
    data, messages = _evidence()
    first = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    corrected = {**data, "phase_b": {"overt": {"activity": "晚饭后散步", "action_taken": True,
                 "completion_status": "complete", "actual_duration_minutes": 20}},
                 "m4_contract": dict(data["m4_contract"])}
    corrected_messages = messages + [_message(27, 27, "user", "更正：我实际走了二十分钟。")]
    corrected["m4_contract"]["phase_b_quote"] = "更正：我实际走了二十分钟。"

    later = normalize(corrected, corrected_messages, session_id="chat-a", cycle_id="cycle-a",
                      assistant_message_id=27, existing={"phase_c": first["phase_c"],
                                                          "confirmation_message_id": 21})

    assert later["chain_confirmation_status"] == "unconfirmed"
    assert later["confirmation_message_id"] is None
    assert later["phase_c"]["_m4_contract"]["facts_hash"] != first["phase_c"]["_m4_contract"]["facts_hash"]


@pytest.mark.asyncio
async def test_cycle_messages_exclude_only_messages_before_a_trusted_prior_cycle_confirmation(goal_api):
    _, db, _ = goal_api
    from app.database_v2_schema import metadata as schema
    from app.models import ConversationMessage
    from sqlalchemy import insert

    messages = [
        ConversationMessage(id=30, conversation_id=1, position=1, role="user", content="上一周期的执行。"),
        ConversationMessage(id=31, conversation_id=1, position=2, role="assistant", content="上一周期总结。"),
        ConversationMessage(id=32, conversation_id=1, position=3, role="user", content="我确认上一周期网页记录。"),
        ConversationMessage(id=33, conversation_id=1, position=4, role="user", content="本周期重新开始。"),
        ConversationMessage(id=34, conversation_id=1, position=5, role="assistant", content="请说这次执行。"),
    ]
    db.add_all(messages)
    await db.execute(insert(schema.tables["ai_decision_logs"]), {
        "conversation_id": 1, "turn_id": "32", "module_name": "module_4",
        "decision_type": "user_confirmation", "decision_value": {},
    })
    await db.commit()

    current = await cycle_messages(db, messages)

    assert [message.id for message in current] == [33, 34]


def test_education_before_abc_confirmation_does_not_open_coping_gate():
    data, messages = _evidence()
    education = next(message for message in messages if message.id == 22)
    messages = [message for message in messages if message.id != 22]
    messages.insert(4, _message(22, 19, education.role, education.content))

    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)

    assert values["chain_confirmation_status"] == "confirmed"
    assert values["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS[:2])


@pytest.mark.parametrize(("decision", "difficulty"), [(1, "none"), (3, "unknown")])
def test_no_difficulty_or_adjustment_does_not_require_a_coping_strategy(decision, difficulty):
    data, messages = _evidence(decision=decision, difficulty=difficulty)
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)

    assert values["next_coping_strategy"] is None
    assert values["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS)
