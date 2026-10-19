"""The user's recent daily records as background facts for the coach.

Records are filled in outside the chat. Saving one does not start a turn; the
coach simply knows the recent days on its next reply, the way it knows the
saved goal card.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import select

from .models import STATUS_COMPLETED, AssessmentEntry
from .schedule_dates import local_day

DAYS = 7
_ACTIVITY_SCORES = ("emotion", "achievement", "connection", "enjoyment", "importance")
_DAY_SCORES = ("completion_rate", "activity_level", "overall_mood", "social_connection", "approach_vs_avoidance")


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
