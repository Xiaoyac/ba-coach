"""/api/auth — registration, login, logout.

    POST /api/auth/register  create an account (and its clinical profile)
    POST /api/auth/login     exchange credentials for a bearer token
    POST /api/auth/logout    revoke the calling token
    GET  /api/auth/me        who am I

Registration writes two rows in one transaction: a `user_accounts` row holding
the credentials, and a `user_profile` row in the externally-owned business
schema. `user_accounts.profile_uuid` carries the latter's `uuid`, and that
value is what every clinical table keys on as `user_id`. Splitting them is not
a preference — the business schema has nowhere to put a password (see
`app.models` on `UserAccount`).

Writing to `user_profile` is an INSERT of a row this app owns the identity of,
never a schema change: `BizBase` is not in `create_all`, so nothing here can
create, alter or drop that table.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from hmac import compare_digest
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..identity import CallerIdentity, require_caller
from ..account_identity import (
    find_account_by_username,
    normalize_account,
)
from ..models import (
    RegistrationInvite,
    AccountEmail,
    AccountSettings,
    AuthSession,
    UserAccount,
    _utcnow,
)
from ..models_business import UserProfile
from ..workflow_state import derived_current_module
from ..schemas import (
    AccountInfo,
    AdminResetRequest,
    AuthResponse,
    BirthYearRequest,
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
)
from ..security import (
    hash_password,
    hash_token,
    needs_rehash,
    new_session_token,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

def _set_value(values: list[str]) -> str | None:
    """Render a list for a MySQL SET column, or None when empty.

    SET columns take a comma-joined string; an empty list must become NULL
    rather than "", which MySQL would store as the empty set and read back as
    a one-element list containing "".
    """
    return ",".join(values) if values else None


async def _issue_session(db: AsyncSession, account: UserAccount) -> tuple[str, AuthSession]:
    """Mint a token for `account` and persist only its hash."""
    settings = get_settings()
    token = new_session_token()
    session = AuthSession(
        account_id=account.id,
        token_hash=hash_token(token),
        expires_at=_utcnow() + timedelta(seconds=settings.auth_session_ttl_seconds),
    )
    db.add(session)
    return token, session


async def _account_info(db: AsyncSession, account: UserAccount) -> AccountInfo:
    from ..birth_years import needs_birth_year
    from .. import v2_profile
    if v2_profile.enabled():
        from ..database_v2_schema import user_profile
        from types import SimpleNamespace
        row = (await db.execute(select(user_profile.c.nickname).where(user_profile.c.uuid == account.profile_uuid))).first()
        profile = SimpleNamespace(nickname=row[0]) if row else None
    else:
        profile = (await db.execute(select(UserProfile).where(UserProfile.uuid == account.profile_uuid))).scalar_one_or_none()
    account_settings = (
        await db.execute(
            select(AccountSettings).where(AccountSettings.account_id == account.id)
        )
    ).scalar_one_or_none()
    account_email = (
        await db.execute(
            select(AccountEmail).where(AccountEmail.account_id == account.id)
        )
    ).scalar_one_or_none()
    return AccountInfo(
        birth_year_required=await needs_birth_year(db, account.profile_uuid),
        username=account.username,
        nickname=profile.nickname if profile else None,
        profile_uuid=account.profile_uuid,
        current_module=(
            await derived_current_module(db, subject_id=account.profile_uuid)
            if profile else None
        ),
        role=account_settings.role if account_settings else "user",
        email=account_email.email if account_email else None,
        email_verified=bool(account_email and account_email.verified_at),
        email_required=False,
        email_delivery_available=False,
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    username = normalize_account(payload.username)
    nickname = payload.nickname.strip()
    from ..invitations import claim_invitation
    await claim_invitation(db, payload.invitation_code)
    password_hash = hash_password(payload.password)

    if await find_account_by_username(db, username) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="登录账号已被使用")
    from .. import v2_profile
    profile = UserProfile(
        nickname=nickname,
        age=payload.age,
        birth_year=payload.birth_year,
        living_status=payload.living_status,
        physical_condition=_set_value(payload.physical_condition),
        behavior_taboo=_set_value(payload.behavior_taboo),
        current_module="开场",
    )
    if v2_profile.enabled():
        import uuid
        from types import SimpleNamespace
        profile = SimpleNamespace(uuid=str(uuid.uuid4()))
        await v2_profile.create(db, profile.uuid, payload)
    else:
        db.add(profile)
    try:
        await db.flush()
        account = UserAccount(
            username=username,
            password_hash=password_hash,
            profile_uuid=profile.uuid,
        )
        db.add(account)
        await db.flush()
        db.add(AccountSettings(account_id=account.id))
        await db.flush()
        token, session = await _issue_session(db, account)
        await db.execute(update(RegistrationInvite).where(RegistrationInvite.code == payload.invitation_code)
            .values(used_by_account_id=account.id))
        account.last_login_at = _utcnow()
        await db.commit()
    except IntegrityError:
        # The pre-checks produce precise messages in the ordinary case. The DB
        # constraints decide a concurrent race and return a neutral conflict.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="登录账号刚刚被其他人使用，请更换后重试",
        ) from None

    logger.info(
        "registered login account %s with nickname %s -> profile %s",
        username,
        nickname,
        profile.uuid,
    )
    return AuthResponse(
        token=token,
        expires_at=session.expires_at,
        account=await _account_info(db, account),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    account = await find_account_by_username(db, payload.username)

    # Same message and same code whether the username is unknown or the
    # password is wrong — the pair is what is being rejected, and saying which
    # half failed turns the login form into an account-existence oracle.
    if account is None or not verify_password(account.password_hash, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码不正确"
        )

    # The one moment the plaintext exists — upgrade the stored hash if the
    # cost parameters have moved on since it was written.
    if needs_rehash(account.password_hash):
        account.password_hash = hash_password(payload.password)

    token, session = await _issue_session(db, account)
    account.last_login_at = _utcnow()
    await db.commit()

    return AuthResponse(
        token=token,
        expires_at=session.expires_at,
        account=await _account_info(db, account),
    )


@router.put("/birth-date", include_in_schema=False)
async def retired_birth_date(caller: CallerIdentity = Depends(require_caller)):
    raise HTTPException(410, "已改为只填写出生年份，请刷新页面后重试")


@router.put("/birth-year", response_model=AccountInfo)
async def set_birth_year(payload: BirthYearRequest,
                         caller: CallerIdentity = Depends(require_caller),
                         db: AsyncSession = Depends(get_db)) -> AccountInfo:
    from .. import v2_profile
    from ..birth_years import age_from_year
    from ..schemas import ProfileUpdate
    if v2_profile.enabled():
        await v2_profile.patch(db, caller.subject_id, ProfileUpdate(birth_year=payload.birth_year))
    else:
        profile = (await db.execute(select(UserProfile).where(UserProfile.uuid == caller.subject_id))).scalar_one()
        profile.birth_year = payload.birth_year
        profile.age = age_from_year(payload.birth_year)
        await db.commit()
    return await _account_info(db, caller.account)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    caller: CallerIdentity = Depends(require_caller),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Change the signed-in account's own password.

    There is deliberately no master password or override string accepted
    here: this endpoint is reachable by anyone who can reach the API, so a
    hardcoded value that satisfies `current_password` would be a universal key
    to every account — and in this app an account is somebody's clinical
    record. Operator-initiated resets go through `scripts/reset_password.py`,
    which requires access to the machine and the database rather than just to
    the URL.
    """
    account = caller.account

    if not verify_password(account.password_hash, payload.current_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="当前密码不正确"
        )

    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="新密码不能与当前密码相同"
        )

    account.password_hash = hash_password(payload.new_password)

    # Every *other* session dies. Changing a password is how someone responds
    # to "I think somebody else has access", and that is meaningless if the
    # other party's token keeps working. The caller's own session survives so
    # they are not bounced to the sign-in screen by their own action.
    for session in list(account.sessions):
        if session.id != caller.session.id:
            await db.delete(session)

    await db.commit()


# --- Operator reset ---------------------------------------------------------
#
# Failed-attempt throttle. In-process and therefore per-worker, which is a real
# limitation (N workers means N times the budget) but still turns an online
# brute force from "as fast as the network allows" into something that trips an
# alarm in the log almost immediately. The key itself is what actually makes
# guessing infeasible; this is defence in depth and a place for the warning to
# be logged.
_ADMIN_MAX_FAILURES = 5
_ADMIN_LOCKOUT_SECONDS = 15 * 60
_admin_failures: list[float] = []


def _admin_locked_out() -> bool:
    now = monotonic()
    # Drop attempts older than the window, so the budget refills over time
    # instead of locking the endpoint out permanently after five typos.
    _admin_failures[:] = [t for t in _admin_failures if now - t < _ADMIN_LOCKOUT_SECONDS]
    return len(_admin_failures) >= _ADMIN_MAX_FAILURES


@router.post("/admin/reset", status_code=status.HTTP_204_NO_CONTENT)
async def admin_reset_password(
    payload: AdminResetRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Reset any account's password using the operator key.

    This is the endpoint that replaces the idea of a master password accepted
    in the ordinary change-password form. The difference is not cosmetic: this
    one is disabled unless a key is configured, requires a long random secret
    rather than a memorable phrase, is rate limited, logs every attempt, and
    cannot be reached by anyone poking at the normal sign-in UI.
    """
    settings = get_settings()
    configured = settings.admin_reset_key

    # No key configured: behave exactly as if this route did not exist, so a
    # default deployment does not advertise that an admin reset path is there
    # to be attacked.
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not Found"
        )

    # A short key would make the throttle the only thing standing between the
    # internet and every account. Refuse to run rather than pretend to be safe.
    if len(configured) < 32:
        logger.error(
            "ADMIN_RESET_KEY is shorter than 32 characters; refusing to serve "
            "the operator reset endpoint"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin reset is misconfigured",
        )

    if _admin_locked_out():
        logger.warning("admin reset locked out after repeated failures")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="尝试次数过多，请稍后再试",
        )

    # Constant-time: a plain `!=` leaks the length of the matching prefix
    # through timing, which is enough to recover a secret one character at a
    # time given sufficient samples.
    if not compare_digest(payload.admin_key, configured):
        _admin_failures.append(monotonic())
        logger.warning(
            "admin reset rejected: bad key (target username=%r)", payload.username
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="管理员密钥不正确"
        )

    account = await find_account_by_username(db, payload.username)

    if account is None:
        # The caller already proved they hold the operator key, so naming a
        # missing account tells them nothing they could not learn anyway — and
        # a vague error here would just make a typo hard to diagnose.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="找不到该用户名"
        )

    account.password_hash = hash_password(payload.new_password)

    # Every session dies, including any the account holder currently has. An
    # operator reset is what happens when access needs to be taken back.
    revoked = len(account.sessions)
    for session in list(account.sessions):
        await db.delete(session)

    await db.commit()

    # Deliberately loud, and with no secret in it: this is the audit trail for
    # an action that bypasses the account holder's own password.
    logger.warning(
        "admin reset succeeded for username=%r (%d sessions revoked)",
        account.username,
        revoked,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    caller: CallerIdentity = Depends(require_caller),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke the token this request authenticated with.

    Only this session — other devices stay signed in, which is what a "log out"
    button on one of them should mean.
    """
    await db.delete(caller.session)
    await db.commit()


@router.get("/me", response_model=AccountInfo)
async def me(
    caller: CallerIdentity = Depends(require_caller),
    db: AsyncSession = Depends(get_db),
) -> AccountInfo:
    return await _account_info(db, caller.account)


@router.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def logout_everywhere(
    caller: CallerIdentity = Depends(require_caller),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke every session for this account, including the current one."""
    for session in list(caller.account.sessions):
        await db.delete(session)
    await db.commit()
