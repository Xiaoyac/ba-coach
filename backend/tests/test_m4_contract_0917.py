from types import SimpleNamespace

import pytest

from app.m4_contract import STEPS, cycle_messages, normalize, repeated_decision_matches
from test_goal_overview import goal_api


def _message(identifier, position, role, content):
    return SimpleNamespace(id=identifier, position=position, role=role, content=content)


@pytest.mark.parametrize("action,text", [
    ("end", "我确认这次复盘，决定结束这个目标，不再开启下一周期。"),
    ("pause", "我理解了，决定先暂停这个目标，保留历史。"),
    ("continue", "这正是我的决定，我继续同一目标并开启下一执行周期。"),
    ("adjust", "我决定调整同一目标，把每次时长改成五分钟。"),
])
def test_repeated_effective_decision_can_confirm_m4(action, text):
    assert repeated_decision_matches(text, action)


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


def test_later_recap_wording_does_not_revoke_confirmed_abc_snapshot():
    data, messages = _evidence()
    first = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    later_messages = messages + [
        _message(27, 27, "assistant", "这次复盘已收到，接下来按你的决定继续。"),
        _message(28, 28, "user", "我理解了，继续观察这次行动带来的感受。"),
    ]
    later_data = {**data, "ai_abc_chain_summary": "这次复盘已收到，接下来按你的决定继续。",
                  "m4_contract": {**data["m4_contract"],
                                  "summary_quote": "这次复盘已收到，接下来按你的决定继续。",
                                  "understanding_quote": "我理解了，继续观察这次行动带来的感受。"}}
    later = normalize(later_data, later_messages, session_id="chat-a", cycle_id="cycle-a",
                      assistant_message_id=28,
                      existing={"phase_c": first["phase_c"], "confirmation_message_id": 21})

    assert later["chain_confirmation_status"] == "confirmed"
    assert later["confirmation_message_id"] == 21
    assert later["phase_c"]["_m4_contract"]["facts_hash"] == first["phase_c"]["_m4_contract"]["facts_hash"]


def test_omitted_later_extraction_keeps_verified_m4_snapshot():
    data, messages = _evidence()
    first = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    omitted = {"m4_contract": {"chain_status": "unconfirmed"}}
    existing = {
        **{key: first[key] for key in ("phase_a", "phase_b", "phase_c", "scenario_type",
                                       "execution_result", "chain_confirmation_status",
                                       "ba_reeducation_content", "next_coping_strategy",
                                       "review_decision", "review_summary")},
        "confirmation_message_id": 21,
    }
    later = normalize(omitted, messages, session_id="chat-a", cycle_id="cycle-a",
                      assistant_message_id=28,
                      existing={**existing, "phase_c": first["phase_c"]})

    assert later["chain_confirmation_status"] == "confirmed"
    assert later["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS)


def test_omitted_closing_review_keeps_verified_decision_and_summary():
    data, messages = _evidence()
    first = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    omitted = {"m4_contract": {"chain_status": "unconfirmed"}}
    existing = {
        **{key: first[key] for key in ("phase_a", "phase_b", "phase_c", "scenario_type",
                                       "execution_result", "chain_confirmation_status",
                                       "ba_reeducation_content", "next_coping_strategy",
                                       "review_decision", "review_summary")},
        "confirmation_message_id": 21,
    }
    later = normalize(omitted, messages, session_id="chat-a", cycle_id="cycle-a",
                      assistant_message_id=28,
                      existing={**existing, "phase_c": first["phase_c"]})

    assert later["review_decision"] == first["review_decision"]
    assert later["review_summary"] == first["review_summary"]
    assert later["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS)


def test_recap_protective_wording_and_fallback_activity_do_not_change_decision():
    data, messages = _evidence()
    first = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    messages = messages + [
        _message(27, 27, "user", "我决定继续，遇到下雨改为在楼道走五分钟。"),
        _message(28, 28, "user", "我确认以上决定不变，不要替我改变决定。"),
        _message(29, 29, "assistant", "收到，我按原决定记录。"),
    ]
    later = normalize({"m4_contract": {}}, messages, session_id="chat-a", cycle_id="cycle-a",
                      assistant_message_id=29,
                      existing={"phase_a": first["phase_a"], "phase_b": first["phase_b"],
                                "phase_c": first["phase_c"], "scenario_type": first["scenario_type"],
                                "execution_result": first["execution_result"],
                                "chain_confirmation_status": "confirmed", "review_decision": 1,
                                "review_summary": first["review_summary"],
                                "confirmation_message_id": 21})

    evidence = later["phase_c"]["_m4_contract"]["evidence"]
    assert evidence["decision_quote"]["message_id"] == 25
    assert later["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS)


def test_pending_early_decision_and_summary_do_not_freeze_later_valid_evidence():
    data, messages = _evidence()
    early_messages = [message for message in messages if message.id != 23]
    first = normalize(data, early_messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    assert first["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS[:2])
    later_messages = early_messages + [
        _message(27, 27, "user", "我理解先行动再观察感受。我决定继续按这个计划。"),
        _message(28, 28, "assistant", "复盘：本次已完成，下一步按决定执行。"),
    ]
    values = normalize(data, later_messages, session_id="chat-a", cycle_id="cycle-a",
                       assistant_message_id=28, existing=first)

    assert values["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS)
    assert values["phase_c"]["_m4_contract"]["evidence"]["decision_quote"]["message_id"] == 27
    assert values["phase_c"]["_m4_contract"]["evidence"]["review_summary_quote"]["message_id"] == 28


def test_repeated_understanding_after_recap_keeps_prior_verified_order():
    data, messages = _evidence()
    pending = {**data, "m4_contract": {**data["m4_contract"], "review_summary_quote": None}}
    first = normalize(pending, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    assert first["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS[:4])
    messages += [_message(27, 27, "user", "我理解先行动再观察感受。")]
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a",
                       assistant_message_id=27, existing=first)
    contract = values["phase_c"]["_m4_contract"]
    assert contract["completed_steps"] == list(STEPS)
    assert contract["evidence"]["understanding_quote"]["message_id"] == 23
    assert contract["evidence"]["decision_quote"]["message_id"] == 25


def test_recap_and_decision_before_first_understanding_stay_unconfirmed():
    data, messages = _evidence()
    messages = [message for message in messages if message.id != 23]
    messages += [_message(27, 27, "user", "我理解先行动再观察感受。")]
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=27)
    assert "review_decision_made" not in values["phase_c"]["_m4_contract"]["completed_steps"]
    assert values["review_decision"] is None


def test_explicit_later_decision_change_does_not_restore_old_review_summary():
    data, messages = _evidence()
    first = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    messages.append(_message(27, 27, "user", "我撤回继续的决定，改为暂停这个目标。"))
    later = normalize({"m4_contract": {}}, messages, session_id="chat-a", cycle_id="cycle-a",
                      assistant_message_id=27,
                      existing={**{key: first[key] for key in ("phase_a", "phase_b", "phase_c",
                                                                "scenario_type", "execution_result",
                                                                "chain_confirmation_status",
                                                                "ba_reeducation_content",
                                                                "next_coping_strategy", "review_decision",
                                                                "review_summary")},
                                "confirmation_message_id": 21})

    contract = later["phase_c"]["_m4_contract"]
    assert later["review_decision"] is None
    assert later["review_summary"] is None
    assert "decision_quote" not in contract["evidence"]
    assert contract["completed_steps"] == list(STEPS[:4])


def test_verified_m4_snapshot_cannot_be_reused_across_cycle_scope():
    data, messages = _evidence()
    first = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    existing = {"phase_c": first["phase_c"], "confirmation_message_id": 21,
                "chain_confirmation_status": "confirmed"}
    later = normalize({"m4_contract": {}}, messages, session_id="chat-b", cycle_id="cycle-b",
                      assistant_message_id=28, existing=existing)

    assert later["chain_confirmation_status"] == "unconfirmed"
    assert later["confirmation_message_id"] is None


def test_same_result_explicit_abc_correction_revokes_old_snapshot():
    data, messages = _evidence()
    first = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    messages = messages + [_message(27, 27, "user", "更正：我实际是在室内完成的，不是晚饭后散步。")]
    later = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=28,
                      existing={"phase_a": first["phase_a"], "phase_b": first["phase_b"],
                                "phase_c": first["phase_c"], "scenario_type": first["scenario_type"],
                                "execution_result": first["execution_result"],
                                "chain_confirmation_status": "confirmed",
                                "confirmation_message_id": 21})

    assert later["chain_confirmation_status"] == "unconfirmed"


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


def test_same_result_c_emotion_change_without_correction_word_revokes_old_snapshot():
    data, messages = _evidence(scenario="C")
    first = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    corrected_messages = messages + [_message(27, 27, "user", "C阶段的心情改为明显改善。")]
    later = normalize(data, corrected_messages, session_id="chat-a", cycle_id="cycle-a",
                      assistant_message_id=27, existing={"phase_c": first["phase_c"],
                                                          "confirmation_message_id": 21})

    assert later["chain_confirmation_status"] == "unconfirmed"
    assert later["confirmation_message_id"] is None


def test_evidence_quote_is_canonical_when_extractor_adds_courtesy_prefix():
    data, messages = _evidence()
    education = "谢谢你确认。从这次经历能看到：先行动，再观察感受和结果。"
    messages = [message if message.id != 22 else _message(22, 22, "assistant", education)
                for message in messages]
    data = {**data, "ba_reeducation_content": "从这次经历能看到：先行动，再观察感受和结果。",
            "m4_contract": {**data["m4_contract"], "education_quote": education}}
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)

    assert values["ba_reeducation_content"] == education
    assert values["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS)


def test_quote_matching_ignores_only_whitespace_and_keeps_source_span():
    data, messages = _evidence()
    source = "BA教育：先行动，再观察\n\n感受和结果。"
    messages = [message if message.id != 22 else _message(22, 22, "assistant", source)
                for message in messages]
    data = {
        **data,
        "m4_contract": {
            **data["m4_contract"],
            "education_quote": "BA教育：先行动，再观察感受和结果。",
        },
    }
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)

    evidence = values["phase_c"]["_m4_contract"]["evidence"]["education_quote"]
    assert evidence["quote"] == source
    assert values["ba_reeducation_content"] == source
    assert values["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS)


def test_quote_matching_rejects_changed_word_even_when_shape_is_similar():
    data, messages = _evidence()
    data = {
        **data,
        "m4_contract": {
            **data["m4_contract"],
            "education_quote": "BA教育：先行动，再观察感觉和结果。",
        },
    }
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)

    assert values["ba_reeducation_content"] is None
    assert values["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS[:2])


def test_summary_quote_must_be_from_assistant_even_when_user_forges_label():
    data, messages = _evidence()
    summary = data["m4_contract"]["summary_quote"]
    messages = [message for message in messages if message.id != 20]
    messages.append(_message(20, 20, "user", summary))
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)

    assert values["chain_confirmation_status"] == "unconfirmed"
    assert "abc_chain_completed" not in values["phase_c"]["_m4_contract"]["completed_steps"]


def test_requoting_same_summary_message_does_not_change_facts_hash():
    data, messages = _evidence()
    first = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)
    # The extractor may choose a shorter quote from the same immutable
    # assistant message; the source message remains the same evidence anchor.
    data = {**data, "ai_abc_chain_summary": "总结：晚饭后散步十分钟" ,
            "m4_contract": {**data["m4_contract"],
                            "summary_quote": "总结：晚饭后散步十分钟"}}
    later = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26,
                      existing={"phase_c": first["phase_c"], "confirmation_message_id": 21})

    assert later["phase_c"]["_m4_contract"]["facts_hash"] == first["phase_c"]["_m4_contract"]["facts_hash"]


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


def test_repeated_same_decision_keeps_first_effective_source_quote():
    data, messages = _evidence()
    repeated = "我决定继续按这个计划。"
    messages.extend([
        _message(27, 27, "user", repeated),
        _message(28, 28, "assistant", "复盘：本次已完成，下一步按决定执行。"),
    ])
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=28)

    evidence = values["phase_c"]["_m4_contract"]["evidence"]["decision_quote"]
    assert evidence["message_id"] == 25
    assert values["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS)


def test_three_repeated_decisions_still_keep_first_source_quote():
    data, messages = _evidence()
    repeated = "我决定继续按这个计划。"
    messages.extend([
        _message(27, 27, "user", repeated),
        _message(28, 28, "user", repeated),
        _message(29, 29, "assistant", "复盘：本次已完成，下一步按决定执行。"),
    ])
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=29)

    evidence = values["phase_c"]["_m4_contract"]["evidence"]["decision_quote"]
    assert evidence["message_id"] == 25


def test_withdrawal_without_new_decision_clears_old_source_quote():
    data, messages = _evidence()
    messages.extend([_message(27, 27, "user", "我撤回继续的决定，先不做任何下一步。")])
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)

    assert "decision_quote" not in values["phase_c"]["_m4_contract"]["evidence"]
    assert values["review_decision"] is None


def test_understanding_and_decision_in_one_user_turn_can_share_source_position():
    data, messages = _evidence()
    messages = [message for message in messages if message.id not in {23, 25, 26}]
    same_turn = "我理解先行动再观察感受。我决定继续按这个计划。"
    messages.extend([
        _message(23, 23, "user", same_turn),
        _message(26, 26, "assistant", "复盘：本次已完成，下一步按决定执行。"),
    ])
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=26)

    evidence = values["phase_c"]["_m4_contract"]["evidence"]["decision_quote"]
    assert evidence["message_id"] == 23
    assert values["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS)


@pytest.mark.parametrize("decision_text", [
    "我想把这个目标结束掉，保留本轮历史，后面不再自动继续。",
    "我想暂停这个目标，保留本轮历史。",
])
def test_repeated_end_or_pause_decision_does_not_trigger_false_change_marker(decision_text):
    data, messages = _evidence()
    messages = [message for message in messages if message.id not in {25, 26}]
    messages.extend([
        _message(25, 25, "user", decision_text),
        _message(27, 27, "user", decision_text),
        _message(28, 28, "assistant", "复盘：本次已完成，按你的决定收尾。"),
    ])
    data = {
        **data,
        "review_decision": 4,
        "review_summary": "复盘：本次已完成，按你的决定收尾。",
        "m4_contract": {
            **data["m4_contract"],
            "decision_quote": decision_text,
            "review_summary_quote": "复盘：本次已完成，按你的决定收尾。",
        },
    }
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=28)

    evidence = values["phase_c"]["_m4_contract"]["evidence"]["decision_quote"]
    assert evidence["message_id"] == 25
    assert values["phase_c"]["_m4_contract"]["completed_steps"] == list(STEPS)


def test_explicit_decision_change_moves_source_to_new_direction():
    data, messages = _evidence()
    changed = "我撤回继续的决定，改为结束这个目标。"
    messages.extend([
        _message(27, 27, "user", changed),
        _message(28, 28, "assistant", "复盘：本次完成，目标按你的新决定收尾。"),
    ])
    data = {
        **data,
        "review_decision": 4,
        "review_summary": "复盘：本次完成，目标按你的新决定收尾。",
        "m4_contract": {
            **data["m4_contract"],
            "decision_quote": "结束这个目标。",
            "review_summary_quote": "复盘：本次完成，目标按你的新决定收尾。",
        },
    }
    values = normalize(data, messages, session_id="chat-a", cycle_id="cycle-a", assistant_message_id=28)

    evidence = values["phase_c"]["_m4_contract"]["evidence"]["decision_quote"]
    assert evidence["message_id"] == 27
    assert values["review_decision"] == 4
