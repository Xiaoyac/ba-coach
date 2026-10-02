"""Regressions from two production turns that kept asking for a date."""
import pytest
import dataclasses

from app.answer_validator import validate_answer
from app.schemas import Message
from app.graph import get_graph
from app.providers.base import StreamDelta
from app.turn_continuity import (
    defers_scheduling, repeats_previous_question,
)


PREVIOUS = "那我们先定一个最近能做的计划：你打算在哪一天、大概几点去动一动呢？"
HISTORY = [Message(role="user", content="有一点运动量，但是不多"),
           Message(role="assistant", content=PREVIOUS)]


@pytest.mark.parametrize("current", ["没想好", "没想好老大", "我还不知道。", "暂时不确定"])
def test_uncertainty_after_scheduling_question_is_recognized(current):
    assert defers_scheduling(HISTORY, current)


@pytest.mark.parametrize("current", ["没想好，不过明天可以", "不知道几点合适，你建议呢？",
    "我不是没想好", "不确定是不是周三，周四有空", "明天晚上七点", "没想好，先说其他的"])
def test_no_keyword_based_override_of_corrections_or_new_information(current):
    assert not defers_scheduling(HISTORY, current)


def test_only_adjacent_visible_assistant_supplies_context():
    pending = HISTORY + [Message(role="user", content="新的问题")]
    assert not defers_scheduling(pending, "不知道")
    assert not defers_scheduling([], "不知道")


@pytest.mark.parametrize("reply", [PREVIOUS,
    "没关系，你晚上大概几点比较方便出门动一动？",
    "你最近哪天晚上比较方便，可以试试出去动一动？",
    "是吃完晚饭后一会儿，还是睡前一段时间？你想选哪个时间段？"])
def test_repeated_date_collection_is_review_not_pass_or_canned_replacement(reply):
    result = validate_answer(reply=reply, module="module_2", evidence_ids=[],
                             history=HISTORY, current_user="没想好老大")
    assert result["status"] == "review"
    assert "repeated_deferred_schedule" in {item["code"] for item in result["findings"]}
    assert result["semantic_verified"] is False
    assert result["llm_calls"] == 0


def test_respectful_pause_or_new_question_does_not_raise_repeat_diagnostic():
    for reply in ["可以先不定时间。你有什么顾虑吗？", "没关系，我们可以以后再说。"]:
        result = validate_answer(reply=reply, module="module_2", evidence_ids=[],
                                 history=HISTORY, current_user="没想好")
        assert result["status"] == "passed"
    assert repeats_previous_question("好的。" + PREVIOUS, HISTORY)
    assert not repeats_previous_question("你有什么顾虑吗？", HISTORY)
    assert not repeats_previous_question(PREVIOUS, HISTORY + [Message(role="user", content="再问我一次")])


@pytest.mark.parametrize("stream", [False, True])
async def test_graph_records_loop_without_rewriting_or_extra_generation(context, provider, monkeypatch, stream):
    context = dataclasses.replace(context, stream=stream)
    session = await context.store.get_or_create(None)
    for message in HISTORY:
        await context.store.append(session.session_id, message)
    calls = []

    async def repeat_stream(**kwargs):
        calls.append(kwargs)
        yield StreamDelta(kind="content", text=PREVIOUS)

    original = provider.complete

    async def repeat_complete(**kwargs):
        calls.append(kwargs)
        completion = await original(**kwargs)
        return dataclasses.replace(completion, text=PREVIOUS)

    monkeypatch.setattr(provider, "stream", repeat_stream)
    monkeypatch.setattr(provider, "complete", repeat_complete)
    state = {"session_id": session.session_id, "user_input": "没想好老大", "forced_module": "module_2"}
    deltas = []
    if stream:
        async for kind, value in get_graph().astream(state, context=context, stream_mode=["custom", "values"]):
            if kind == "values":
                result = value
            elif value.get("type") == "delta":
                deltas.append(value["text"])
        assert "".join(deltas) == PREVIOUS
    else:
        result = await get_graph().ainvoke(state, context=context)
    assert result["final_response"] == PREVIOUS
    assert len(calls) == 1
    validation = result["telemetry"]["answer_validator"]
    assert validation["mode"] == "diagnostic_only"
    assert validation["status"] == "review"
    assert "repeated_deferred_schedule" in {finding["code"] for finding in validation["findings"]}
