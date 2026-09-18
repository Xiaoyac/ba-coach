"""Normalized profile reads/writes. Old API names are projections, not slots."""
from datetime import datetime, timezone
import uuid
from fastapi import HTTPException
from sqlalchemy import select, insert, update, delete
from .database_v2_schema import metadata as schema
from .models import UserAccount, AccountHandle, AccountSettings
from .providers import configured_providers
from .schemas import ProfileOut
from .birth_dates import age_on


def enabled():
    from .config import get_settings
    return get_settings().database_schema_version == "v2"


async def read(db, user_id):
    profiles, prefs, supporters, constraints = [schema.tables[n] for n in
        ("user_profile", "user_preferences", "user_supporters", "user_activity_constraints")]
    profile = (await db.execute(select(profiles).where(profiles.c.uuid == user_id))).mappings().one_or_none()
    if profile is None:
        raise HTTPException(404, "档案不存在")
    preference = (await db.execute(select(prefs).where(prefs.c.user_id == user_id))).mappings().one_or_none() or {}
    people = (await db.execute(select(supporters).where(supporters.c.user_id == user_id)
        .order_by(supporters.c.position))).mappings().all()
    limits = (await db.execute(select(constraints).where(constraints.c.user_id == user_id,
        constraints.c.status == "active", constraints.c.confirmation_status != "rejected"))).mappings().all()
    account = (await db.execute(select(UserAccount.id).where(UserAccount.profile_uuid == user_id))).scalar_one_or_none()
    handle = await db.get(AccountHandle, account) if account else None
    settings = await db.get(AccountSettings, account) if account else None
    from .workflow_state import derived_current_module
    return ProfileOut(nickname=profile["nickname"], birth_date=profile["birth_date"],
        age=age_on(profile["birth_date"]) if profile["birth_date"] else profile["reported_age"],
        living_status=profile["living_status"], tag=handle.tag if handle else None,
        display_id=handle.full_username if handle else None,
        communication_preference=preference.get("communication_style"),
        reminder_frequency=preference.get("reminder_frequency"), reminder_time_slot=preference.get("reminder_time_slot"),
        reminder_window={"start_minute": preference["reminder_start_minute"], "end_minute": preference["reminder_end_minute"]}
            if preference.get("reminder_start_minute") is not None and preference.get("reminder_end_minute") is not None else None,
        activity_environment=preference.get("activity_environment"), activity_social=preference.get("activity_social"),
        activity_intensity=preference.get("activity_atmosphere"),
        supporters=[{"relation": p["relation"] or "未说明", "nickname": p["nickname"], "influence": p["influence"]} for p in people],
        has_supporter=bool(people),
        physical_condition=[r["content"] for r in limits if r["category"] == "身体"],
        behavior_taboo=[r["content"] for r in limits if r["category"] == "行为边界"],
        content_taboo=[r["content"] for r in limits if r["category"] == "话题边界"],
        preferred_provider=settings.preferred_provider if settings else "deepseek",
        available_providers={k: v for k, v in configured_providers().items() if k in {"deepseek", "doubao"}},
        current_module=await derived_current_module(db, subject_id=user_id))


async def create(db, user_id, payload):
    await db.execute(insert(schema.tables["user_profile"]), {"uuid": user_id, "nickname": payload.nickname,
        "birth_date": payload.birth_date, "birth_year": payload.birth_date.year,
        "reported_age": payload.age, "age_reported_at": datetime.now(timezone.utc).replace(tzinfo=None),
        "living_status": payload.living_status})
    await db.execute(insert(schema.tables["user_preferences"]), {"user_id": user_id,
        "communication_style": payload.communication_preference})
    await db.execute(insert(schema.tables["user_module_one_state"]), {"user_id": user_id, "completed_steps": []})
    await replace_constraints(db, user_id, "身体", "safety_limit", payload.physical_condition)
    await replace_constraints(db, user_id, "行为边界", "user_boundary", payload.behavior_taboo)


async def replace_constraints(db, user_id, category, kind, values):
    table = schema.tables["user_activity_constraints"]
    await db.execute(update(table).where(table.c.user_id == user_id, table.c.category == category,
        table.c.status == "active").values(status="inactive"))
    for value in values or []:
        await db.execute(insert(table), {"id": str(uuid.uuid4()), "user_id": user_id,
            "category": category, "constraint_type": kind, "content": value,
            "source_type": "profile_form", "confirmation_status": "confirmed"})


async def patch(db, user_id, payload):
    from .routes.profile import _load_account_handle, _load_account_settings, _account_id
    from .routes.auth import normalize_display_name
    current = await read(db, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if any(k.startswith("supporter1_") or k.startswith("supporter2_") for k in changes):
        raise HTTPException(422, "请刷新网页，通过支持者列表编辑")
    profile = schema.tables["user_profile"]
    await db.execute(select(profile.c.uuid).where(profile.c.uuid == user_id).with_for_update())
    if "has_supporter" in changes and bool(changes["has_supporter"]) != bool(changes.get("supporters", current.supporters)):
        raise HTTPException(422, "支持者标记由支持者列表决定")
    updates = {k: changes[k] for k in ("nickname", "living_status") if k in changes}
    if "age" in changes:
        if current.birth_date or "birth_date" in changes:
            raise HTTPException(422, "年龄由出生日期计算，请修改出生日期")
        updates.update(reported_age=changes["age"], age_reported_at=datetime.now(timezone.utc).replace(tzinfo=None))
    if "birth_date" in changes:
        birthday = payload.birth_date
        updates.update(birth_date=birthday, birth_year=birthday.year, reported_age=age_on(birthday),
            age_reported_at=datetime.now(timezone.utc).replace(tzinfo=None))
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.execute(update(profile).where(profile.c.uuid == user_id).values(**updates))
    preferences = schema.tables["user_preferences"]
    mapping = {"communication_preference": "communication_style", "activity_intensity": "activity_atmosphere",
        "activity_environment": "activity_environment", "activity_social": "activity_social",
        "reminder_frequency": "reminder_frequency", "reminder_time_slot": "reminder_time_slot"}
    updates = {new: changes[old] for old, new in mapping.items() if old in changes}
    if "reminder_window" in changes:
        window = payload.reminder_window
        if window and window.start_minute >= window.end_minute:
            raise HTTPException(422, "提醒开始时间必须早于结束时间")
        updates.update(reminder_start_minute=window.start_minute if window else None,
                       reminder_end_minute=window.end_minute if window else None)
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        existing = (await db.execute(select(preferences.c.user_id).where(preferences.c.user_id == user_id))).scalar_one_or_none()
        if not existing:
            await db.execute(insert(preferences), {"user_id": user_id})
        await db.execute(update(preferences).where(preferences.c.user_id == user_id).values(**updates))
    if "supporters" in changes:
        people = schema.tables["user_supporters"]
        await db.execute(delete(people).where(people.c.user_id == user_id))
        for i, person in enumerate(changes["supporters"] or []):
            await db.execute(insert(people), {"id": str(uuid.uuid4()), "user_id": user_id, "position": i, **person})
    for field, category, kind in (("physical_condition", "身体", "safety_limit"),
            ("behavior_taboo", "行为边界", "user_boundary"), ("content_taboo", "话题边界", "user_boundary")):
        if field in changes:
            await replace_constraints(db, user_id, category, kind, changes[field])
    if "nickname" in changes or "tag" in changes:
        nickname = (changes.get("nickname") or current.nickname or "").strip()
        tag = changes.get("tag", current.tag)
        if not nickname or "#" in nickname or not tag:
            raise HTTPException(422, "请填写有效昵称和标签")
        handle = await _load_account_handle(db, user_id)
        if not handle:
            handle = AccountHandle(account_id=await _account_id(db, user_id))
            db.add(handle)
        handle.base_username, handle.normalized_base, handle.tag = nickname, normalize_display_name(nickname), tag
    if "preferred_provider" in changes:
        settings = await _load_account_settings(db, user_id)
        if not settings:
            settings = AccountSettings(account_id=await _account_id(db, user_id))
            db.add(settings)
        settings.preferred_provider = changes["preferred_provider"] or "deepseek"
    await db.commit()
    return await read(db, user_id)
