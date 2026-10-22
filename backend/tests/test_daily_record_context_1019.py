from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.daily_record_context import daily_record_line, recent_daily_records
from app.models import STATUS_COMPLETED, STATUS_SKIPPED, ActivityLog, AssessmentEntry
from test_goal_overview import goal_api

TODAY = date(2026, 10, 19)


def _entry(subject_id, on, status=STATUS_COMPLETED, **values):
    return AssessmentEntry(subject_id=subject_id, recorded_on=on, timezone="Asia/Shanghai",
                           status=status, scale_version=2, **values)


@pytest.mark.asyncio
async def test_recent_completed_records_are_newest_first_and_scoped(goal_api):
    _, db, _ = goal_api
    walk = _entry("a", TODAY, reflection_note="散步后轻松一些")
    walk.activities.append(ActivityLog(position=0, time_slot="晚饭后", activity="散步", emotion=4, enjoyment=3))
    db.add_all([
        walk,
        _entry("a", TODAY - timedelta(days=2), reflection_note="前天"),
        _entry("a", TODAY - timedelta(days=1), status=STATUS_SKIPPED),   # declined that day
        _entry("a", TODAY - timedelta(days=7), reflection_note="一周前"),  # outside the week
        _entry("b", TODAY, reflection_note="别人"),                       # another user
    ])
    await db.commit()

    records = await recent_daily_records(db, "a", TODAY)

    assert [r["date"] for r in records] == ["2026-10-19", "2026-10-17"]
    assert records[0] == {"date": "2026-10-19", "scale_max": 5,
                          "reflection_note": "散步后轻松一些",
                          "activities": [{"time_slot": "晚饭后", "activity": "散步", "emotion": 4, "enjoyment": 3}]}


@pytest.mark.asyncio
async def test_no_records_adds_no_context_line(goal_api):
    _, db, _ = goal_api
    assert await daily_record_line(db, "a", TODAY) is None


@pytest.mark.asyncio
async def test_saved_record_reaches_the_coach_context(goal_api, monkeypatch):
    import app.daily_record_context as module
    from app.v2_workflow import clinical_context
    _, db, _ = goal_api
    monkeypatch.setattr(module, "local_day", lambda *args, **kwargs: TODAY)
    db.add(_entry("a", TODAY, reflection_note="今天有点累"))
    await db.commit()

    lines = await clinical_context(async_sessionmaker(db.bind, expire_on_commit=False), "a", None)

    record = next(line for line in lines if line.startswith("用户自行填写的每日记录"))
    assert "今天有点累" in record and "overall_mood" not in record


def _measurement(subject_id, on, **values):
    from app.models import DailyMeasurement
    answers = dict(sleep_hours=7.5, sleep_quality=2, completion=4, activity_level=3, overall_mood=4,
                   energetic=3, peaceful=4, secure=4, low_mood=2, worthless=1, irritable=2, tired=3,
                   stressed=2, lonely=1, vigorous_count=0, vigorous_minutes=0, moderate_count=1,
                   moderate_minutes=30, light_count=2, light_minutes=40)
    return DailyMeasurement(subject_id=subject_id, recorded_on=on, timezone="Asia/Shanghai", **{**answers, **values})


@pytest.mark.asyncio
async def test_every_daily_measurement_reaches_the_coach_context(goal_api):
    from app.v2_workflow import clinical_context
    _, db, _ = goal_api
    db.add_all([
        _measurement("a", TODAY, overall_mood=5),
        _measurement("a", TODAY - timedelta(days=90), sleep_hours=6),  # long ago is still included
        _measurement("b", TODAY, overall_mood=1),                       # another user
    ])
    await db.commit()

    lines = await clinical_context(async_sessionmaker(db.bind, expire_on_commit=False), "a", None)

    line = next(line for line in lines if line.startswith("用户自行填写的每日测量"))
    header, *rows = line.split("\n")
    assert "全部2天" in header and "日期|昨晚睡眠小时|" in header and header.endswith("轻度运动分钟")
    assert rows == ["2026-10-19|7.5|2|4|3|5|3|4|4|2|1|2|3|2|1|0|0|1|30|2|40",
                    "2026-07-21|6|2|4|3|4|3|4|4|2|1|2|3|2|1|0|0|1|30|2|40"]


@pytest.mark.asyncio
async def test_no_measurements_adds_no_context_line(goal_api):
    from app.daily_record_context import daily_measurement_line
    _, db, _ = goal_api
    assert await daily_measurement_line(db, "a") is None
