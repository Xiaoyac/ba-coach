"""The user's daily records and 每日测量 as background facts for the coach.

Both are filled in outside the chat. Saving one does not start a turn; the
coach simply knows them on its next reply, the way it knows the saved goal
card. Records cover the recent week; 每日测量 covers every day ever filled.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import select

from .models import MEASURE_FIELDS, STATUS_COMPLETED, AssessmentEntry, DailyMeasurement
from .schedule_dates import local_day

DAYS = 7
_ACTIVITY_SCORES = ("emotion", "achievement", "connection", "enjoyment", "importance")
_DAY_SCORES = ("social_connection", "approach_vs_avoidance")


def _clip(text, limit):
    return text if not text or len(text) <= limit else text[:limit] + "…"


def _entry(entry: AssessmentEntry) -> dict:
    day = {"date": entry.recorded_on.isoformat(), "scale_max": 5 if entry.scale_version == 2 else 10}
    day.update({key: getattr(entry, key) for key in _DAY_SCORES if getattr(entry, key) is not None})
    if entry.reflection_note:
        day["reflection_note"] = _clip(entry.reflection_note, 300)
    activities = []
    for item in entry.activities[:12]:
        activity = {"time_slot": item.time_slot, "activity": item.activity}
        activity.update({key: getattr(item, key) for key in _ACTIVITY_SCORES if getattr(item, key) is not None})
        if item.note:
            activity["note"] = _clip(item.note, 200)
        activities.append({key: value for key, value in activity.items() if value != ""})
    if activities:
        # Activity ratings are always 0–5, whatever the day's scale.
        day["activities"] = activities
    return day


async def recent_daily_records(db, subject_id: str, today: date | None = None) -> list[dict]:
    """Completed records of the last DAYS local days, newest first."""
    today = today or local_day()
    rows = (await db.execute(select(AssessmentEntry).where(
        AssessmentEntry.subject_id == subject_id, AssessmentEntry.status == STATUS_COMPLETED,
        AssessmentEntry.recorded_on > today - timedelta(days=DAYS), AssessmentEntry.recorded_on <= today,
    ).order_by(AssessmentEntry.recorded_on.desc()))).scalars().all()
    return [_entry(row) for row in rows]


async def daily_record_line(db, subject_id: str, today: date | None = None) -> str | None:
    records = await recent_daily_records(db, subject_id, today)
    if not records:
        return None
    return f"用户自行填写的每日记录（最近{DAYS}天，活动评分0–5）：" + json.dumps(records, ensure_ascii=False)


# One column per answer, in MEASURE_FIELDS order; one row per day keeps every
# day affordable in the context.
_MEASURE_COLUMNS = (
    "昨晚睡眠小时", "昨晚睡眠质量(1很好 2较好 3较差 4很差)",
    "完成程度(1完全没有完成…5全部完成)", "身体活动(1几乎没有…5非常多)", "整体心情(1非常糟糕…5非常愉悦)",
    "有活力的", "安宁的", "有安全感的", "低落或沮丧的", "感觉自己没有价值", "烦躁的", "疲倦的", "感觉有压力的", "孤独的",
    "剧烈运动次数", "剧烈运动分钟", "中等强度运动次数", "中等强度运动分钟", "轻度运动次数", "轻度运动分钟",
)
assert len(_MEASURE_COLUMNS) == len(MEASURE_FIELDS)


async def daily_measurement_line(db, subject_id: str) -> str | None:
    """Every 每日测量 the user has submitted, newest first."""
    rows = (await db.execute(select(DailyMeasurement).where(DailyMeasurement.subject_id == subject_id)
                             .order_by(DailyMeasurement.recorded_on.desc()))).scalars().all()
    if not rows:
        return None
    table = "\n".join("|".join([row.recorded_on.isoformat(), *(f"{getattr(row, name):g}" for name in MEASURE_FIELDS)])
                      for row in rows)
    return (f"用户自行填写的每日测量（全部{len(rows)}天，新到旧；睡眠指测量日前一晚，其余指测量日当天；"
            "情绪各项1完全不符合…5非常符合）。每行字段：日期|" + "|".join(_MEASURE_COLUMNS) + "\n" + table)
