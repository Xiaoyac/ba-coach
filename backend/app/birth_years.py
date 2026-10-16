"""Explicit birth years; ages are calendar-year estimates, never full birthdays."""
from datetime import date, datetime, timedelta, timezone
import re


def today() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def age_from_year(year: int, on: date | None = None) -> int:
    return (on or today()).year - year


def validate_birth_year(value) -> int:
    if isinstance(value, str) and re.fullmatch(r'\d{4}', value):
        value = int(value)
    if type(value) is not int or not 1000 <= value <= 9999:
        raise ValueError('请填写四位出生年份')
    if not 10 <= age_from_year(value) <= 120:
        raise ValueError('请核对出生年份，当前支持按年份估算为 10–120 岁')
    return value


async def needs_birth_year(db, user_id: str) -> bool:
    from sqlalchemy import select
    from .v2_profile import enabled
    if enabled():
        from .database_v2_schema import user_profile
        row = (await db.execute(select(user_profile.c.birth_year).where(user_profile.c.uuid == user_id))).first()
    else:
        from .models_business import UserProfile
        row = (await db.execute(select(UserProfile.birth_year).where(UserProfile.uuid == user_id))).first()
    return bool(row and row[0] is None)
