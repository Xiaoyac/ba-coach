"""Explicit birthdays and derived age; no inferred or default birth dates."""
from datetime import date, datetime, timedelta, timezone
import re


def today() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def age_on(birthday: date, on: date | None = None) -> int:
    on = on or today()
    return on.year - birthday.year - ((on.month, on.day) < (birthday.month, birthday.day))


def validate_birth_date(value) -> date:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            raise ValueError("请填写有效的出生日期") from None
    if type(value) is not date:
        raise ValueError("请填写出生日期（年-月-日）")
    if value > today():
        raise ValueError("出生日期不能晚于今天")
    if not 10 <= age_on(value) <= 120:
        raise ValueError("请核对出生日期，当前支持 10–120 岁")
    return value


async def needs_birth_date(db, user_id: str) -> bool:
    from sqlalchemy import select
    from .v2_profile import enabled
    if enabled():
        from .database_v2_schema import user_profile
        row = (await db.execute(select(user_profile.c.birth_date, user_profile.c.reported_age)
            .where(user_profile.c.uuid == user_id))).first()
    else:
        from .models_business import UserProfile
        row = (await db.execute(select(UserProfile.birth_date, UserProfile.age)
            .where(UserProfile.uuid == user_id))).first()
    return bool(row and row[0] is None and row[1] == 10)
