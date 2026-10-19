"""Write relative day words in a plan schedule as calendar dates (明天 -> 10.20).

A plan read days later must not keep saying 明天. The anchor is the day the
wording was said (the user's message), never the time of a later extraction.
Recurring wording (每周三, 每天) is left untouched.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Shanghai"

_RELATIVE_DAYS = {"今天": (0, ""), "今日": (0, ""), "今晚": (0, "晚上"), "今早": (0, "早上"),
                  "明天": (1, ""), "明日": (1, ""), "明晚": (1, "晚上"), "明早": (1, "早上"),
                  "后天": (2, ""), "大后天": (3, "")}
# Longest alternatives first so 大后天 is never read as 后天.
_RELATIVE = re.compile("|".join(sorted(_RELATIVE_DAYS, key=len, reverse=True)))
_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_WEEK = re.compile(r"(下|这|本)个?(?:周|星期|礼拜)([一二三四五六日天])")


def local_day(moment: datetime | None = None, timezone_name: str = DEFAULT_TIMEZONE) -> date:
    """The local calendar day of a server timestamp (naive values are UTC)."""
    moment = moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo(timezone_name)).date()


def _label(day: date) -> str:
    return f"{day.month}.{day.day}"


def date_relative_days(text, anchor: date):
    """Replace 今天/明天/后天/下周三… with month.day labels relative to anchor."""
    if not isinstance(text, str) or not text:
        return text

    def relative(match):
        offset, part = _RELATIVE_DAYS[match.group(0)]
        return _label(anchor + timedelta(days=offset)) + part

    def weekday(match):
        index = _WEEKDAYS[match.group(2)]
        monday = anchor - timedelta(days=anchor.weekday())
        if match.group(1) == "下":
            monday += timedelta(days=7)
        return f"{_label(monday + timedelta(days=index))}（周{match.group(2)}）"

    return _RELATIVE.sub(relative, _WEEK.sub(weekday, text))


def has_relative_days(text) -> bool:
    return isinstance(text, str) and bool(_RELATIVE.search(text) or _WEEK.search(text))


def date_schedule_from_messages(schedule, messages, *, timezone_name: str = DEFAULT_TIMEZONE):
    """Date a schedule by the latest user message that said it.

    The literal schedule is preferred as the source; otherwise the latest user
    message containing the same relative word. Without any source the wording
    stays as it is rather than guessing a day.
    """
    if not has_relative_days(schedule):
        return schedule
    users = [m for m in messages or [] if getattr(m, "role", None) == "user"
             and isinstance(getattr(m, "created_at", None), datetime)]
    tokens = [m.group(0) for m in _RELATIVE.finditer(schedule)] + [m.group(0) for m in _WEEK.finditer(schedule)]
    source = next((m for m in reversed(users) if schedule in (m.content or "")), None) or next(
        (m for m in reversed(users) if any(token in (m.content or "") for token in tokens)), None)
    if source is None:
        return schedule
    return date_relative_days(schedule, local_day(source.created_at, timezone_name))
