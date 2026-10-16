"""Model time guesses never become structured plans without user evidence."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.models import ConversationMessage
from app.temporal_evidence import normalize_plan_time, sourced_plan_datetime
from app.v2_workflow import create_goal_from_agent_dialogue, persist_record, record_values
from test_extraction_freshness_0916 import seed_m2
from test_goal_contract_0914 import primary_payload, seed_goal_dialogue
from test_goal_overview import goal_api


ANCHOR = datetime(2026, 9, 29, 16, 30, tzinfo=timezone.utc)


def source(content, *, role="user", created_at=ANCHOR):
    return SimpleNamespace(role=role, content=content, created_at=created_at)


@pytest.mark.parametrize("schedule,expected", [
    ("明天晚上8点", datetime(2026, 10, 1, 20)),
    ("昨天14:30散步", datetime(2026, 9, 29, 14, 30)),
    ("2026年10月3日下午三点半", datetime(2026, 10, 3, 15, 30)),
    ("2027-01-01T20:15", datetime(2027, 1, 1, 20, 15)),
])
def test_dates_come_from_original_user_timestamp(schedule, expected):
    # The source was sent on Sep 30 in China; extraction can happen days later.
    assert sourced_plan_datetime(schedule, [source("我打算" + schedule)],
        timezone_name="Asia/Shanghai") == expected


def test_naive_database_timestamp_is_server_utc():
    assert sourced_plan_datetime("明天20:00", [source("明天20:00", created_at=ANCHOR.replace(tzinfo=None))],
        timezone_name="Asia/Shanghai") == datetime(2026, 10, 1, 20)


@pytest.mark.parametrize("original,schedule", [
    ("大概明天晚上8点左右", "明天晚上8点"),
    ("不要明天晚上8点，改成后天晚上9点", "明天晚上8点"),
    ("不要明天晚上8点，改成后天晚上9点", "后天晚上9点"),
    ("明天晚上8点这个安排撤回", "明天晚上8点"),
    ("明天晚上8点不行", "明天晚上8点"),
    ("大后天晚上8点", "后天晚上8点"),
    ("大前天晚上8点", "前天晚上8点"),
    ("明天晚上8点半", "明天晚上8点"),
    ("明天晚上8点15分", "明天晚上8点"),
    ("明天20:00:30", "明天20:00"),
    ("明天20:00 UTC", "明天20:00"),
    ("明天晚上8点散步，晚上9点复盘", "明天晚上8点"),
    ("2026年10月10日20:00", "2026年10月1"),
    ("2026年10月10日20:00，11日也可能", "2026年10月10日20:00"),
])
def test_schedule_substring_cannot_discard_original_qualifiers(original, schedule):
    assert sourced_plan_datetime(schedule, [source(original)], timezone_name="Asia/Shanghai") is None


@pytest.mark.parametrize("schedule", [
    "今天晚上12点", "今天晚上12:00", "今天晚上12:30", "今天晚上十二点半",
    "今天晚间12点", "今天晚间12:10", "今天傍晚12点", "今天傍晚12:00",
])
def test_evening_twelve_cannot_be_silently_converted_to_noon(schedule):
    assert sourced_plan_datetime(schedule, [source(schedule)], timezone_name="Asia/Shanghai") is None


def test_unrelated_review_frequency_does_not_invalidate_single_exact_time():
    assert sourced_plan_datetime("明天晚上8点", [source("我选择明天晚上8点散步；每周日复盘。")],
        timezone_name="Asia/Shanghai") == datetime(2026, 10, 1, 20)


@pytest.mark.parametrize("schedule", [
    "明天下午", "最近", "前几天", "明天8点", "明天晚上8点左右", "每天20:00", "每周三20:00",
    "10月1日20:00", "2026年2月30日20:00", "明天或后天20:00", "2026年10月1日明天20:00",
    "2026年10月1日、10月2日20:00", "明天20:00到21:00", "大后天20:00",
    "明天20:00:30", "明天20:00 UTC", "2026-10-01T20:00+08:00", "明天25:00",
])
def test_ambiguous_or_unsupported_time_stays_unstructured(schedule):
    assert sourced_plan_datetime(schedule, [source(schedule)], timezone_name="Asia/Shanghai") is None


@pytest.mark.parametrize("messages,zone", [
    ([source("明天20:00", role="assistant")], "Asia/Shanghai"),
    ([source("我没有提到时间")], "Asia/Shanghai"),
    ([source("明天20:00", created_at=None)], "Asia/Shanghai"),
    ([source("明天20:00"), source("明天20:00", created_at=datetime(2026, 10, 1))], "Asia/Shanghai"),
    ([source("明天20:00")], None),
    ([source("明天20:00")], "Invalid/Zone"),
])
def test_missing_or_nonunique_user_source_cannot_produce_time(messages, zone):
    assert sourced_plan_datetime("明天20:00", messages, timezone_name=zone) is None


@pytest.mark.parametrize("schedule", ["2026-03-08 02:30", "2026-11-01 01:30"])
def test_nonexistent_or_ambiguous_dst_wall_time_is_rejected(schedule):
    assert sourced_plan_datetime(schedule, [source(schedule)], timezone_name="America/New_York") is None


def test_model_datetime_is_ignored_even_when_parseable():
    raw = {"schedule_text": "明天20:00", "target_activity_time": "1999-01-01 01:23:45",
           "scheduled_start_at": "2045-01-01 02:34:56", "activity_content": "散步"}
    result = normalize_plan_time(raw, [source("明天20:00")], timezone_name="Asia/Shanghai",
        output_key="target_activity_time")
    assert result == {"schedule_text": "明天20:00", "target_activity_time": datetime(2026, 10, 1, 20),
                      "activity_content": "散步"}
    assert raw["target_activity_time"] == "1999-01-01 01:23:45"


@pytest.mark.parametrize("data", [
    {"location": "楼下", "target_activity_time": "1999-01-01 01:23:45"},
    {"schedule_text": "明天20:00", "scheduled_start_at": "1999-01-01 01:23:45"},
])
def test_omitted_or_unchanged_schedule_preserves_saved_time_by_omission(data):
    existing = {"schedule_text": "明天20:00", "scheduled_start_at": datetime(2026, 10, 1, 20)}
    result = normalize_plan_time(data, [], timezone_name="Asia/Shanghai", existing=existing)
    assert "scheduled_start_at" not in result and "target_activity_time" not in result
    assert {**existing, **result}["scheduled_start_at"] == existing["scheduled_start_at"]


def test_changed_ambiguous_schedule_clears_old_standard_time_but_keeps_original_text():
    result = normalize_plan_time({"schedule_text": "明天下午", "target_activity_time": "2026-10-01 15:00:00"},
        [source("明天下午")], timezone_name="Asia/Shanghai",
        existing={"schedule_text": "明天20:00", "scheduled_start_at": datetime(2026, 10, 1, 20)})
    assert result == {"schedule_text": "明天下午", "scheduled_start_at": None}


def test_record_mapper_cannot_bypass_time_source_guard():
    assert record_values("module_2", {"target_activity_time": "2026-10-01 20:00:00",
        "scheduled_start_at": datetime(2026, 10, 1, 20), "timezone": "America/New_York",
        "schedule_text": "明天下午"}) == {"schedule_text": "明天下午"}


@pytest.mark.asyncio
async def test_goal_creation_ignores_model_time_and_derives_from_user(goal_api):
    _, db, _ = goal_api
    assistant_id = await seed_goal_dialogue(db)
    await db.execute(update(ConversationMessage).where(ConversationMessage.id == assistant_id - 1).values(
        content="我选择明天晚上8点散步，想让自己更有精力；每周日复盘。", created_at=ANCHOR))
    payload = primary_payload()
    payload.update(target_activity_content="散步", schedule_text="明天晚上8点",
                   target_activity_time="1999-01-01 01:23:45")
    payload["goal_proposal"].update(selection_quote="我选择明天晚上8点散步", activity_quote="散步")
    result = await create_goal_from_agent_dialogue(db, session_id="new-chat-a", user_id="a",
        data=payload, completed_steps=[], assistant_message_id=assistant_id)
    assert result is not None
    plans = schema.tables["module_two_record"]
    row = (await db.execute(select(plans).where(plans.c.id == result["plan_id"]))).mappings().one()
    assert row["schedule_text"] == "明天晚上8点"
    assert row["scheduled_start_at"] == datetime(2026, 10, 1, 20)


@pytest.mark.asyncio
@pytest.mark.parametrize("schedule,expected", [
    ("明天晚上8点", datetime(2026, 10, 1, 20)), ("明天下午", None),
])
async def test_draft_update_replaces_or_clears_old_time_from_source(goal_api, schedule, expected):
    _, db, _ = goal_api
    await seed_m2(db)
    plans = schema.tables["module_two_record"]
    await db.execute(update(plans).where(plans.c.id == "m2-draft").values(
        scheduled_start_at=datetime(2026, 9, 15, 8)))
    await db.execute(insert(ConversationMessage), [
        {"id": 21, "conversation_id": 1, "position": 2, "role": "user",
         "content": "我想" + schedule + "散步", "created_at": ANCHOR},
        {"id": 22, "conversation_id": 1, "position": 3, "role": "assistant",
         "content": "收到安排", "created_at": ANCHOR},
    ])
    await db.commit()
    await persist_record(async_sessionmaker(db.bind, expire_on_commit=False), module="module_2",
        user_id="a", cycle_id="m2-cycle", data={"schedule_text": schedule,
            "target_activity_time": "1999-01-01 01:23:45",
            "_source_session_id": "chat-a", "_source_assistant_message_id": 22})
    row = (await db.execute(select(plans).where(plans.c.id == "m2-draft"))).mappings().one()
    assert row["schedule_text"] == schedule
    assert row["scheduled_start_at"] == expected
