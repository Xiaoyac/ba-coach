"""Derive optional plan timestamps from authenticated user wording only.

The model may retain a natural-language schedule, but never supplies the
structured timestamp. Unsupported wording stays useful as text and is not
rounded, completed, or converted using the time of a later extraction.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .pa_schedule import clock


_FULL_DATE = re.compile(r"(?<!\d)(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?!\d)")
_RELATIVE_DATE = re.compile(r"今天|明天|后天|昨天|前天")
_RELATIVE_DAYS = {"今天": 0, "明天": 1, "后天": 2, "昨天": -1, "前天": -2}
_UNSUPPORTED = re.compile(
    r"每|工作日|周末|星期|周[一二三四五六日天]|"
    r"大概|大约|左右|约|可能|也许|不一定|或者|或是|最近|近期|前几天|"
    r"不是|不要|不在|取消|改天|改成|改到|改为|不去|不做|之前|之后|前后|大前天|大后天|"
    r"[~～—–]|\d\s*[至到]\s*\d"
)
_SOURCE_QUALIFIER = re.compile(
    r"大概|大约|左右|约|可能|也许|不一定|不确定|或者|或是|最近|近期|前几天|"
    r"不是|不要|不在|取消|改天|改成|改到|改为|不去|不做|之前|之后|前后|大前天|大后天|"
    r"不行|不能|别|没空|不方便|撤回|作废|放弃|算了|待定|暂定|"
    r"[~～—–]|\d\s*[至到]\s*\d"
)
_MIDNIGHT_WORDING = re.compile(r"(?:晚上|晚间|傍晚|夜里|夜间|半夜|深夜|午夜|夜晚)\s*(?:0?12|十二)(?=[:：点时])")
_UNSUPPORTED_PRECISION = re.compile(
    r"\d[:：]\d{2}[:：]\d|秒|UTC|GMT|[+-]\d{2}:?\d{2}|时区|北京时间|当地时间", re.I
)


def sourced_plan_datetime(schedule_text, messages, *, timezone_name):
    """Return a local wall datetime, or None when precise evidence is absent.

    V2 plan timestamps are local wall times paired with a separate timezone
    column. Conversation timestamps are server UTC, including legacy naive
    values read from the database. Require one source row even for absolute
    dates so a repeated relative phrase never borrows the wrong day's anchor.
    """
    if not isinstance(schedule_text, str) or not schedule_text.strip():
        return None
    schedule = schedule_text.strip()
    if not isinstance(timezone_name, str) or not timezone_name:
        return None
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None
    sources = [message for message in messages or []
               if getattr(message, "role", None) == "user"
               and schedule in (getattr(message, "content", None) or "")]
    if len(sources) != 1:
        return None
    source_text = sources[0].content
    anchor = getattr(sources[0], "created_at", None)
    # A literal substring can still omit "大概", "不要", a half hour, or a
    # retraction. Validate the original message as well as the chosen span.
    # Frequency in a separate clause (e.g. 每周日复盘) is not a qualifier.
    if (not isinstance(anchor, datetime) or _UNSUPPORTED.search(schedule)
            or _SOURCE_QUALIFIER.search(source_text) or _MIDNIGHT_WORDING.search(source_text)
            or _UNSUPPORTED_PRECISION.search(source_text)):
        return None
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    try:
        anchor_day = anchor.astimezone(zone).date()
    except (ValueError, OverflowError):
        return None
    absolute = list(_FULL_DATE.finditer(schedule))
    relative = list(_RELATIVE_DATE.finditer(schedule))
    # An omitted year, multiple dates, or mixed absolute/relative expressions
    # require clarification. A recurring plan has no single start timestamp.
    if len(absolute) + len(relative) != 1:
        return None
    source_absolute = list(_FULL_DATE.finditer(source_text))
    source_relative = list(_RELATIVE_DATE.finditer(source_text))
    if len(source_absolute) + len(source_relative) != 1:
        return None
    if (absolute and (not source_absolute or absolute[0].groups() != source_absolute[0].groups())
            or relative and (not source_relative or relative[0].group() != source_relative[0].group())):
        return None
    without_day = (_FULL_DATE.sub("", schedule) if absolute else _RELATIVE_DATE.sub("", schedule))
    if re.search(r"\d\s*(?:年|月|日|号)|\d[-/]\d", without_day):
        return None
    try:
        day = (date(*map(int, absolute[0].groups())) if absolute else
               anchor_day + timedelta(days=_RELATIVE_DAYS[relative[0].group()]))
    except (ValueError, OverflowError):
        return None
    # The clock parser supports minute precision; never silently discard
    # seconds or a user-written timezone/offset.
    tod = clock(schedule)
    # Multiple clocks or a truncated minute expression cannot be assigned to
    # this plan reliably. Evening 12 is rejected above, never treated as noon.
    if tod is None or clock(source_text) != tod:
        return None
    wall = datetime.combine(day, tod)
    local = wall.replace(tzinfo=zone)
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        return None
    if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != wall:
        return None
    return wall


def normalize_plan_time(data, messages, *, timezone_name, existing=None,
                        output_key="scheduled_start_at"):
    """Copy fields, discard model datetimes, and safely update plan time.

    An omitted/unchanged schedule leaves a saved timestamp untouched. A new
    or changed schedule always derives its timestamp again and explicitly
    clears the old value when that derivation is unsupported. The existing
    confirmation-only snapshot merge remains authoritative at the caller.
    Use output_key='target_activity_time' after extraction's coerce step;
    use the default key immediately before V2 persistence.
    """
    if output_key not in {"target_activity_time", "scheduled_start_at"}:
        raise ValueError("unsupported plan timestamp field")
    values = dict(data)
    values.pop("target_activity_time", None)
    values.pop("scheduled_start_at", None)
    if "schedule_text" not in values:
        return values
    schedule = values.get("schedule_text")
    if existing is not None and schedule == existing.get("schedule_text"):
        return values
    values[output_key] = sourced_plan_datetime(schedule, messages, timezone_name=timezone_name)
    return values
