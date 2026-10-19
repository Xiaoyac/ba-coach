from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import insert

from app.m4_contract import cycle_messages, previous_attempt, round_boundary, attempt_snapshot
from app.schedule_dates import date_relative_days, date_schedule_from_messages
from test_goal_overview import goal_api

MONDAY = date(2026, 10, 19)


@pytest.mark.parametrize("text,expected", [
    ("明天晚上散步", "10.20晚上散步"),
    ("今晚八点", "10.19晚上八点"),
    ("明早", "10.20早上"),
    ("后天下午", "10.21下午"),
    ("大后天", "10.22"),
    ("下周三晚饭后", "10.28（周三）晚饭后"),
    ("这周五", "10.23（周五）"),
    ("每周三晚饭后", "每周三晚饭后"),
    ("每天早上", "每天早上"),
    ("10.20晚上", "10.20晚上"),
    (None, None),
])
def test_relative_days_become_dates(text, expected):
    assert date_relative_days(text, MONDAY) == expected


def _said(position, content, created_at):
    return SimpleNamespace(id=position, position=position, role="user", content=content, created_at=created_at)


def test_schedule_is_dated_by_the_message_that_said_it_not_by_today():
    # 2026-10-18 17:30 UTC is already 10-19 in Shanghai.
    messages = [_said(1, "那就明天晚上散步吧", datetime(2026, 10, 18, 17, 30)),
                _said(3, "好的", datetime(2026, 10, 21, 2, 0))]
    assert date_schedule_from_messages("明天晚上", messages) == "10.20晚上"


def test_schedule_without_a_source_keeps_its_wording():
    messages = [_said(1, "随便聊聊", datetime(2026, 10, 18, 2, 0))]
    assert date_schedule_from_messages("明天晚上", messages) == "明天晚上"


async def _chat(db, modules):
    """Messages with one logged module per turn: (position, role, module)."""
    from app.database_v2_schema import metadata as schema
    from app.models import ConversationMessage
    messages = []
    for position, role, module in modules:
        message = ConversationMessage(id=100 + position, conversation_id=1, position=position, role=role,
                                      content=f"{role}-{position}")
        messages.append(message)
        db.add(message)
        if role == "assistant":
            row = {"module_name": module, "decision_type": "step_completion", "decision_value": {}}
        else:
            row = {"module_name": "module_4", "decision_type": "router_only_decision",
                   "decision_value": {"applied_module": module}}
        await db.execute(insert(schema.tables["ai_decision_logs"]),
                         {"conversation_id": 1, "turn_id": str(message.id), **row})
    await db.commit()
    return messages


def _review(assistant_message_id):
    return {"phase_c": {"_m4_contract": {"version": "m4-20261008-positive-feedback",
                                         "assistant_message_id": assistant_message_id}}}


@pytest.mark.asyncio
async def test_return_to_planning_after_a_review_starts_a_new_attempt(goal_api):
    _, db, _ = goal_api
    messages = await _chat(db, [
        (1, "user", "module_4"), (2, "assistant", "module_4"),      # first review
        (3, "user", "module_2"), (4, "assistant", "module_2"),      # back to planning
        (5, "user", "module_4"), (6, "assistant", "module_4"),      # second review
    ])

    assert await round_boundary(db, 1) == 4
    assert [m.position for m in await cycle_messages(db, messages)] == [5, 6]
    assert await previous_attempt(db, _review(102), 1) is True
    assert await previous_attempt(db, _review(106), 1) is False


@pytest.mark.asyncio
async def test_first_review_keeps_its_planning_history(goal_api):
    _, db, _ = goal_api
    messages = await _chat(db, [
        (1, "user", "module_2"), (2, "assistant", "module_2"),
        (3, "user", "module_4"), (4, "assistant", "module_4"),
    ])

    assert await round_boundary(db, 1) == -1
    assert [m.position for m in await cycle_messages(db, messages)] == [1, 2, 3, 4]
    assert await previous_attempt(db, _review(104), 1) is False


def test_archived_attempt_snapshot_is_json_safe():
    snapshot = attempt_snapshot({"phase_a": {"situation": "下班后"}, "review_decision": 1,
                                 "confirmed_at": datetime(2026, 10, 19), "abc_chain_summary": "总结"})
    assert snapshot["phase_a"] == {"situation": "下班后"} and "confirmed_at" not in snapshot


@pytest.mark.asyncio
async def test_goal_card_form_saves_relative_day_as_a_date(goal_api, monkeypatch):
    from test_goal_card_workspace import setup_card
    import app.schedule_dates as schedule_dates
    client, db, _ = goal_api
    _, _, _, card = await setup_card(db)
    monkeypatch.setattr(schedule_dates, "local_day", lambda *args, **kwargs: MONDAY)
    monkeypatch.setattr("app.config.get_settings",
                        lambda: SimpleNamespace(goal_card_ui_enabled=True, database_schema_version="v2"))
    response = await client.put("/api/program/chat-a/goal-card", json={"card_id": card["id"], "revision": 1,
        "fields": {"activity_content": "散步", "schedule_text": "明天下午", "frequency_text": "每周三次"}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["card"]["fields"]["schedule_text"] == "10.20下午"
    assert "时间安排：10.20下午" in result["submission_text"]
    assert result["card"]["fields"]["frequency_text"] == "每周三次"
