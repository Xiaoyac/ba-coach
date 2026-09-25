"""Conservative, deterministic schedule parsing. Never invent an end time.

Only exact supported expressions become reminders. Relative dates require a
verified user's original message timestamp, not an LLM's guessed datetime.
"""
import re
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BUFFER = timedelta(hours=1)
MAX_LATENESS = timedelta(minutes=30)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def naive_utc(value):
    return utc(value).replace(tzinfo=None)


def number(value):
    if value.isdigit():
        return int(value)
    digits = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
              '六': 6, '七': 7, '八': 8, '九': 9}
    if value in digits:
        return digits[value]
    if '十' in value and value.count('十') == 1:
        left, right = value.split('十')
        if (left and left not in digits) or (right and right not in digits): raise ValueError('unsupported number')
        return (digits[left] if left else 1) * 10 + (digits[right] if right else 0)
    raise ValueError('unsupported number')


def clock(text):
    # Ranges, approximations, alternatives and negations aren't exact schedules.
    if re.search(r'左右|大概|可能|也许|或|取消|不去|不做|改天|不一定|以后|之前|之后|前后|刻|[~～—–]|\d\s*[至到]\s*\d', text):
        return None
    matches = list(re.finditer(r'(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|晚间)?\s*'
        r'([0-9零一二两三四五六七八九十]{1,3})(?::|：|点|时)'
        r'(半|[0-9零一二两三四五六七八九十]{1,3}分?)?', text))
    if len(matches) != 1:
        return None
    m = matches[0]
    try:
        h = number(m[2]); minute = 30 if m[3] == '半' else number(m[3].rstrip('分')) if m[3] else 0
        part = m[1]
        if part in {'下午', '傍晚', '晚上', '晚间'}:
            if not 1 <= h <= 12: return None
            h = h % 12 + 12
        elif part in {'凌晨', '早上', '早晨', '上午'}:
            if not 0 <= h <= 12: return None
            if h == 12:
                if part != '凌晨': return None
                h = 0
        elif part == '中午':
            if not 11 <= h <= 13: return None
        elif ':' not in m[0] and '：' not in m[0] and h < 13:
            return None  # "4点" alone doesn't establish AM/PM.
        return time(h, minute)
    except (ValueError, TypeError):
        return None


def explicit_day(text, anchor):
    dates = re.findall(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?', text)
    if len(dates) == 1:
        try: return date(*map(int, dates[0]))
        except ValueError: return None
    relative = [word for word in ('今天', '明天', '后天', '昨天', '前天') if word in text]
    if len(relative) == 1:
        return anchor + timedelta(days={'今天': 0, '明天': 1, '后天': 2, '昨天': -1, '前天': -2}[relative[0]])
    short = re.findall(r'(?<!\d)(\d{1,2})月(\d{1,2})日', text)
    if len(short) == 1:
        try: return date(anchor.year, *map(int, short[0]))
        except ValueError: return None
    return None


def starts(text, *, anchor, timezone_name, at, days=7):
    """Return aware UTC starts; unknown/ambiguous/DST-fold schedules yield []."""
    try: zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError): return []
    tod = clock(text)
    if tod is None: return []
    today = utc(at).astimezone(zone).date()
    weekdays = None
    if '每天' in text or '每日' in text:
        if re.search(r'每周|工作日|周末|除外|除了|不含', text): return []
        weekdays = set(range(7))
    elif '每周' in text or '每星期' in text:
        if len(re.findall(r'每周|每星期', text)) != 1: return []
        m = re.search(r'每(?:周|星期)([一二三四五六日天](?:[、，,和及](?:周|星期)?[一二三四五六日天])*)', text)
        if not m or re.match(r'[天次到至-]', text[m.end():]): return []
        weekdays = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}
        weekdays = {weekdays[x] for x in m[1] if x in weekdays}
        if re.search(r'除外|除了|不含|隔周|每两周', text): return []
    candidates = [today + timedelta(days=i) for i in range(-1, days)] if weekdays is not None else [
        explicit_day(text, utc(anchor).astimezone(zone).date())]
    result = []
    for day in candidates:
        if day is None or (weekdays is not None and day.weekday() not in weekdays): continue
        wall = datetime.combine(day, tod)
        local = wall.replace(tzinfo=zone)
        # Reject nonexistent and ambiguous local times instead of choosing a fold.
        if local.utcoffset() != local.replace(fold=1).utcoffset(): continue
        if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != wall: continue
        result.append(local.astimezone(timezone.utc))
    return result
