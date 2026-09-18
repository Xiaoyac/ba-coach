"""/api/profile — read and edit the subject's own profile.

    GET   /api/profile   what the coach currently knows about you
    PATCH /api/profile   change it

Registration asks only for what cannot change (age, living situation) plus the
handful the coach needs from its first reply (physical limits, movement
taboos, tone). Everything else — and everything asked at signup — is edited
here, because a healed injury or a shifted preference must not be frozen at
the moment somebody created an account.

The profile spans two tables, and the split is not arbitrary:

* `user_profile` is one of the seven externally-owned tables (see
  `app.models_business`). This module UPDATEs a row it owns the identity of
  and never issues DDL. The columns it refuses to touch are listed on
  `ProfileUpdate`.
* `profile_extensions` (app-owned) holds the answers `user_profile` is
  structurally unable to store: an arbitrary number of supporters with
  free-text relations, and a precise reminder window. See `ProfileExtension`.

Writes go to the extension table first, then project down onto the legacy
columns so anything else reading `user_profile` sees a coarser but current
answer rather than a stale one.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..clinical_store import set_values
from ..birth_dates import age_on
from ..db import get_db
from ..identity import require_subject_id
from ..account_identity import normalize_display_name
from ..models import AccountHandle, AccountSettings, ProfileExtension, UserAccount
from ..models_business import UserProfile
from ..providers import configured_providers
from ..schemas import ProfileOut, ProfileUpdate, ReminderWindow, Supporter
from ..workflow_state import derived_current_module
from ..supporters import effective_supporters, LEGACY_SUPPORTER_FIELDS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])

# Columns stored as MySQL SET, which the API exposes as lists.
_SET_FIELDS = ("physical_condition", "behavior_taboo", "content_taboo")

# Fields that live in `profile_extensions`, not `user_profile`.
_EXTENSION_FIELDS = ("supporters", "reminder_window")
_ACCOUNT_FIELDS = ("preferred_provider",)
_IDENTITY_FIELDS = ("tag",)

# The six buckets `user_profile.reminder_time_slot` allows, as (name, start,
# end) in minutes from midnight. Used only to project a precise window down
# onto the legacy ENUM — the real answer lives in `profile_extensions`.
_LEGACY_SLOTS: tuple[tuple[str, int, int], ...] = (
    ("早晨7-9", 7 * 60, 9 * 60),
    ("上午9-12", 9 * 60, 12 * 60),
    ("中午12-14", 12 * 60, 14 * 60),
    ("下午14-18", 14 * 60, 18 * 60),
    ("傍晚18-21", 18 * 60, 21 * 60),
    ("晚上21-23", 21 * 60, 23 * 60),
)

# The six relations the legacy ENUM accepts. Anything else the subject types
# is kept in the extension table and simply has no legacy counterpart.
_LEGACY_RELATIONS = frozenset({"父母", "恋人", "子女", "朋友", "兄弟姐妹", "同事"})


def _nearest_legacy_slot(start: int) -> str:
    """The ENUM bucket a precise start time falls in, or the closest one."""
    for name, low, high in _LEGACY_SLOTS:
        if low <= start < high:
            return name
    # Outside every bucket (03:00, say) — pick whichever edge is nearest, so
    # anything reading the legacy column still gets a defensible answer.
    return min(
        _LEGACY_SLOTS,
        key=lambda slot: min(abs(start - slot[1]), abs(start - slot[2])),
    )[0]


def _project_onto_legacy_columns(
    profile: UserProfile,
    *,
    supporters: list[Supporter] | None,
    window: ReminderWindow | None,
) -> None:
    """Keep `user_profile` a coarser but *current* view of the real answers.

    The extension table is the source of truth, but `user_profile` is read by
    another system. Leaving its columns at whatever was set before would be
    worse than a lossy update — it would be stale.
    """
    if window is not None:
        profile.reminder_time_slot = _nearest_legacy_slot(window.start_minute)

    if supporters is not None:
        profile.has_supporter = bool(supporters)
        # Only two fit, and only relations the ENUM knows. A supporter with a
        # custom relation still contributes their nickname — a name with no
        # relation is more useful than an empty slot.
        for index, attr in enumerate(("supporter1", "supporter2")):
            entry = supporters[index] if index < len(supporters) else None
            relation = entry.relation if entry else None
            setattr(
                profile,
                f"{attr}_relation",
                relation if relation in _LEGACY_RELATIONS else None,
            )
            setattr(profile, f"{attr}_nickname", entry.nickname if entry else None)
            setattr(profile, f"{attr}_influence", entry.influence if entry else None)


def _to_out(
    profile: UserProfile,
    extension: ProfileExtension | None,
    account_settings: AccountSettings | None,
    handle: AccountHandle | None,
) -> ProfileOut:
    supporters = effective_supporters(profile, extension)
    window = None
    if (
        extension is not None
        and extension.reminder_start_minute is not None
        and extension.reminder_end_minute is not None
    ):
        window = ReminderWindow(
            start_minute=extension.reminder_start_minute,
            end_minute=extension.reminder_end_minute,
        )

    return ProfileOut(
        nickname=profile.nickname,
        tag=handle.tag if handle else None,
        display_id=handle.full_username if handle else None,
        age=age_on(profile.birth_date) if profile.birth_date else profile.age,
        birth_date=profile.birth_date,
        living_status=profile.living_status,
        has_supporter=bool(supporters),
        supporter1_relation=profile.supporter1_relation,
        supporter1_nickname=profile.supporter1_nickname,
        supporter1_influence=profile.supporter1_influence,
        supporter2_relation=profile.supporter2_relation,
        supporter2_nickname=profile.supporter2_nickname,
        supporter2_influence=profile.supporter2_influence,
        communication_preference=profile.communication_preference,
        reminder_frequency=profile.reminder_frequency,
        reminder_time_slot=profile.reminder_time_slot,
        physical_condition=set_values(profile.physical_condition),
        behavior_taboo=set_values(profile.behavior_taboo),
        content_taboo=set_values(profile.content_taboo),
        activity_environment=profile.activity_environment,
        activity_social=profile.activity_social,
        activity_intensity=profile.activity_intensity,
        supporters=supporters,
        reminder_window=window,
        preferred_provider=(
            account_settings.preferred_provider if account_settings else "deepseek"
        ),
        available_providers={
            key: value
            for key, value in configured_providers().items()
            if key in {"deepseek", "doubao"}
        },
        current_module=profile.current_module,
    )


async def _own_profile(db: AsyncSession, subject_id: str) -> UserProfile:
    profile = (
        await db.execute(select(UserProfile).where(UserProfile.uuid == subject_id))
    ).scalar_one_or_none()
    if profile is None:
        # Registration writes the account and the profile in one transaction,
        # so this means the row was deleted underneath a live session.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="档案不存在")
    return profile


async def _load_extension(
    db: AsyncSession, subject_id: str
) -> ProfileExtension | None:
    return (
        await db.execute(
            select(ProfileExtension).where(ProfileExtension.profile_uuid == subject_id)
        )
    ).scalar_one_or_none()


async def _load_account_settings(
    db: AsyncSession, subject_id: str
) -> AccountSettings | None:
    return (
        await db.execute(
            select(AccountSettings)
            .join(UserAccount, UserAccount.id == AccountSettings.account_id)
            .where(UserAccount.profile_uuid == subject_id)
        )
    ).scalar_one_or_none()


async def _load_account_handle(
    db: AsyncSession, subject_id: str
) -> AccountHandle | None:
    return (
        await db.execute(
            select(AccountHandle)
            .join(UserAccount, UserAccount.id == AccountHandle.account_id)
            .where(UserAccount.profile_uuid == subject_id)
        )
    ).scalar_one_or_none()


async def _account_id(db: AsyncSession, subject_id: str) -> int:
    account_id = (
        await db.execute(
            select(UserAccount.id).where(UserAccount.profile_uuid == subject_id)
        )
    ).scalar_one_or_none()
    if account_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    return account_id


@router.get("", response_model=ProfileOut)
async def read_profile(
    response: Response,
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> ProfileOut:
    # This response includes live provider availability derived from the
    # process environment. A cached response can keep a newly configured
    # provider disabled in the UI even after the backend has restarted.
    response.headers["Cache-Control"] = "no-store"
    from .. import v2_profile
    if v2_profile.enabled():
        return await v2_profile.read(db, subject_id)
    profile = await _own_profile(db, subject_id)
    result = _to_out(
        profile,
        await _load_extension(db, subject_id),
        await _load_account_settings(db, subject_id),
        await _load_account_handle(db, subject_id),
    )
    result.current_module = await derived_current_module(db, subject_id=subject_id)
    return result


@router.patch("", response_model=ProfileOut)
async def update_profile(
    payload: ProfileUpdate,
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> ProfileOut:
    from .. import v2_profile
    if v2_profile.enabled():
        try:
            return await v2_profile.patch(db, subject_id, payload)
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "昵称标签已被使用，请刷新后重试") from None
    profile = await _own_profile(db, subject_id)

    # `exclude_unset` is what makes a partial edit partial: a form that only
    # touches the reminder section sends only those keys, and every other
    # column keeps its value. Sending an explicit null still clears a field —
    # that is how "actually, I have no restrictions any more" is expressed.
    changes = payload.model_dump(exclude_unset=True)
    if "age" in changes and (profile.birth_date or "birth_date" in changes):
        raise HTTPException(422, "年龄由出生日期计算，请修改出生日期")
    if "birth_date" in changes:
        from ..birth_dates import age_on
        changes["age"] = age_on(payload.birth_date)
    legacy_edits = LEGACY_SUPPORTER_FIELDS.intersection(changes)
    if "supporters" in changes and legacy_edits:
        raise HTTPException(status_code=422, detail="请只提交 supporters 列表，不要同时修改旧支持者槽位")
    if "supporters" in changes and "has_supporter" in changes and bool(changes["has_supporter"]) != bool(changes["supporters"]):
        raise HTTPException(status_code=422, detail="has_supporter 必须与 supporters 列表一致")
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="没有要修改的内容"
        )

    window = payload.reminder_window
    if window is not None and window.start_minute >= window.end_minute:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="提醒时段的开始时间必须早于结束时间",
        )

    extension = await _load_extension(db, subject_id)
    account_settings = await _load_account_settings(db, subject_id)
    handle = await _load_account_handle(db, subject_id)
    if any(key in changes for key in _EXTENSION_FIELDS):
        if extension is None:
            extension = ProfileExtension(profile_uuid=subject_id)
            db.add(extension)
        if "supporters" in changes:
            extension.supporters = [s.model_dump() for s in (payload.supporters or [])]
        if "reminder_window" in changes:
            extension.reminder_start_minute = window.start_minute if window else None
            extension.reminder_end_minute = window.end_minute if window else None
        _project_onto_legacy_columns(
            profile,
            supporters=payload.supporters if "supporters" in changes else None,
            window=window if "reminder_window" in changes else None,
        )

    if "preferred_provider" in changes:
        if account_settings is None:
            account_settings = AccountSettings(
                account_id=await _account_id(db, subject_id)
            )
            db.add(account_settings)
        account_settings.preferred_provider = payload.preferred_provider or "deepseek"

    if "tag" in changes or "nickname" in changes:
        nickname = (
            payload.nickname.strip()
            if "nickname" in changes and payload.nickname
            else (profile.nickname or "").strip()
        )
        if not nickname or "#" in nickname:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="昵称不能为空且不能包含 #",
            )
        if "tag" in changes and payload.tag is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="五位数字标签不能清空"
            )
        tag = payload.tag if "tag" in changes else (handle.tag if handle else None)
        if handle is not None:
            handle.base_username = nickname
            handle.normalized_base = normalize_display_name(nickname)
            if tag is not None:
                handle.tag = tag
        elif tag is not None:
            handle = AccountHandle(
                account_id=await _account_id(db, subject_id),
                base_username=nickname,
                normalized_base=normalize_display_name(nickname),
                tag=tag,
            )
            db.add(handle)

    for key, value in changes.items():
        if key in _EXTENSION_FIELDS or key in _ACCOUNT_FIELDS or key in _IDENTITY_FIELDS:
            continue  # written above; not a `user_profile` column
        if key in _SET_FIELDS:
            # MySQL SET wants a comma-joined string, and an empty list has to
            # become NULL rather than "" — which MySQL stores as the empty set
            # and reads back as a list containing one empty string.
            value = ",".join(value) if value else None
        elif key == "nickname" and isinstance(value, str):
            value = value.strip() or None
        setattr(profile, key, value)

    # Older clients may edit slots, but must never erase the third and later
    # entries. Synchronize their first-two edit back into the canonical list.
    if legacy_edits:
        had_list = extension is not None and extension.supporters is not None
        existing = effective_supporters(profile, extension)
        for i in ((1,2) if had_list else ()):
            fields = {f"supporter{i}_{part}" for part in ("relation","nickname","influence")}
            if not fields.intersection(legacy_edits):
                continue
            relation = getattr(profile, f"supporter{i}_relation")
            if f"supporter{i}_relation" not in legacy_edits and i <= len(existing):
                relation = existing[i-1].relation
            nickname = getattr(profile, f"supporter{i}_nickname")
            if relation or nickname:
                person = Supporter(relation=relation or "未说明（旧档案）",nickname=nickname,
                    influence=getattr(profile,f"supporter{i}_influence"))
                if i <= len(existing): existing[i-1] = person
                else: existing.append(person)
            elif i <= len(existing):
                # Use full-list writes for removal to avoid shifting another slot.
                raise HTTPException(status_code=422, detail="删除支持者请提交完整 supporters 列表")
        if extension is None:
            extension = ProfileExtension(profile_uuid=subject_id)
            db.add(extension)
        extension.supporters = [s.model_dump() for s in existing]
        if "has_supporter" in changes and bool(changes["has_supporter"]) != bool(existing):
            raise HTTPException(status_code=422, detail="has_supporter 必须与支持者列表一致")
        _project_onto_legacy_columns(profile,supporters=existing,window=None)
    elif "supporters" in changes:
        _project_onto_legacy_columns(profile,supporters=payload.supporters or [],window=None)
    elif "has_supporter" in changes:
        if bool(changes["has_supporter"]) != bool(effective_supporters(profile, extension)):
            raise HTTPException(status_code=422, detail="支持者标记由列表决定，请编辑 supporters 列表")

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="这个昵称与标签组合已被使用",
        ) from None
    logger.info(
        "profile updated for %s… (%s)", subject_id[:8], ", ".join(sorted(changes))
    )
    result = _to_out(profile, extension, account_settings, handle)
    result.current_module = await derived_current_module(db, subject_id=subject_id)
    return result
