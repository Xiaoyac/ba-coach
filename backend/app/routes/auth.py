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

import html
import logging
from datetime import timedelta
from hmac import compare_digest
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..emailer import (
    EmailDeliveryUnavailable,
    email_delivery_configured,
    send_account_email,
)
from ..identity import CallerIdentity, require_caller
from ..account_identity import (
    find_account_by_username,
    normalize_account,
    normalize_display_name,
)
from ..models import (
    AccountEmail,
    AccountEmailToken,
    AccountHandle,
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
    ChangePasswordRequest,
    EmailAddressRequest,
    EmailTokenRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
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

VERIFY_EMAIL = "verify_email"
PASSWORD_RESET = "password_reset"


def _normalise_email(value: object) -> str:
    return str(value).strip().casefold()


async def _issue_email_token(
    db: AsyncSession, *, account_id: int, purpose: str, ttl_seconds: int,
    force: bool = False,
) -> str | None:
    """Create a fresh raw token while storing only its SHA-256 hash.

    One unconsumed token per purpose is enough. Repeated clicks inside the
    cooldown return success without sending another message, which limits
    mailbox spam without revealing whether the address belongs to an account.
    """

    now = _utcnow()
    latest = (
        await db.execute(
            select(AccountEmailToken)
            .where(
                AccountEmailToken.account_id == account_id,
                AccountEmailToken.purpose == purpose,
                AccountEmailToken.consumed_at.is_(None),
            )
            .order_by(AccountEmailToken.created_at.desc())
        )
    ).scalars().first()
    if latest is not None:
        cooldown = get_settings().email_token_cooldown_seconds
        if not force and (now - latest.created_at).total_seconds() < cooldown:
            return None
        latest.consumed_at = now

    raw = new_session_token()
    db.add(
        AccountEmailToken(
            account_id=account_id,
            purpose=purpose,
            token_hash=hash_token(raw),
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
    )
    await db.flush()
    return raw


async def _deliver_security_link(
    *, recipient: str, token: str, purpose: str
) -> None:
    settings = get_settings()
    base = settings.account_link_base_url.rstrip("/")
    if purpose == VERIFY_EMAIL:
        url = f"{base}/verify-email?token={token}"
        subject = "验证你的 BA Coach 邮箱"
        intro = "请点击下面的链接验证邮箱。验证后，这个邮箱才可用于找回密码。"
        action = "验证邮箱"
    else:
        url = f"{base}/reset-password?token={token}"
        subject = "重置你的 BA Coach 密码"
        intro = "我们收到了密码重置请求。链接将在一小时后失效，并且只能使用一次。"
        action = "设置新密码"

    safe_url = html.escape(url, quote=True)
    text = f"{intro}\n\n{url}\n\n如果不是你本人操作，可以忽略这封邮件。"
    markup = (
        '<div style="font-family:system-ui,sans-serif;max-width:560px;margin:auto;'
        'padding:28px;color:#33302b;background:#f7f5f0;border-radius:20px">'
        f'<h2 style="font-size:18px;font-weight:600">{subject}</h2>'
        f'<p style="line-height:1.75">{intro}</p>'
        f'<p><a href="{safe_url}" style="display:inline-block;padding:12px 20px;'
        'border-radius:999px;background:#a56a7a;color:white;text-decoration:none">'
        f'{action}</a></p>'
        '<p style="font-size:12px;line-height:1.6;color:#6f6759">'
        '如果不是你本人操作，可以忽略这封邮件。</p></div>'
    )
    await send_account_email(
        recipient=recipient, subject=subject, text=text, html=markup
    )


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
    profile = (
        await db.execute(
            select(UserProfile).where(UserProfile.uuid == account.profile_uuid)
        )
    ).scalar_one_or_none()
    account_settings = (
        await db.execute(
            select(AccountSettings).where(AccountSettings.account_id == account.id)
        )
    ).scalar_one_or_none()
    handle = (
        await db.execute(
            select(AccountHandle).where(AccountHandle.account_id == account.id)
        )
    ).scalar_one_or_none()
    account_email = (
        await db.execute(
            select(AccountEmail).where(AccountEmail.account_id == account.id)
        )
    ).scalar_one_or_none()
    return AccountInfo(
        username=account.username,
        nickname=profile.nickname if profile else None,
        tag=handle.tag if handle else None,
        display_id=handle.full_username if handle else None,
        profile_uuid=account.profile_uuid,
        current_module=(
            await derived_current_module(db, subject_id=account.profile_uuid)
            if profile else None
        ),
        role=account_settings.role if account_settings else "user",
        email=account_email.email if account_email else None,
        email_verified=bool(account_email and account_email.verified_at),
        email_required=account_email is None,
        email_delivery_available=email_delivery_configured(),
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    username = normalize_account(payload.username)
    nickname = payload.nickname.strip()
    normalized_nickname = normalize_display_name(nickname)
    email = str(payload.email).strip()
    normalized_email = _normalise_email(payload.email)
    password_hash = hash_password(payload.password)

    if await find_account_by_username(db, username) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="登录账号已被使用")
    duplicate_display = (
        await db.execute(
            select(AccountHandle.account_id).where(
                AccountHandle.normalized_base == normalized_nickname,
                AccountHandle.tag == payload.tag,
            )
        )
    ).scalar_one_or_none()
    if duplicate_display is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="这个昵称与标签组合已被使用",
        )
    duplicate_email = (
        await db.execute(
            select(AccountEmail.account_id).where(
                AccountEmail.normalized_email == normalized_email
            )
        )
    ).scalar_one_or_none()
    if duplicate_email is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="这个邮箱已绑定其他账号",
        )

    profile = UserProfile(
        nickname=nickname,
        age=payload.age,
        living_status=payload.living_status,
        communication_preference=payload.communication_preference,
        physical_condition=_set_value(payload.physical_condition),
        behavior_taboo=_set_value(payload.behavior_taboo),
        current_module="开场",
    )
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
        db.add(
            AccountHandle(
                account_id=account.id,
                base_username=nickname,
                normalized_base=normalized_nickname,
                tag=payload.tag,
            )
        )
        db.add(AccountSettings(account_id=account.id))
        db.add(
            AccountEmail(
                account_id=account.id,
                email=email,
                normalized_email=normalized_email,
            )
        )
        verification_token = None
        if email_delivery_configured():
            verification_token = await _issue_email_token(
                db,
                account_id=account.id,
                purpose=VERIFY_EMAIL,
                ttl_seconds=get_settings().email_verification_ttl_seconds,
            )
        await db.flush()
        token, session = await _issue_session(db, account)
        account.last_login_at = _utcnow()
        await db.commit()
    except IntegrityError:
        # The pre-checks produce precise messages in the ordinary case. The DB
        # constraints decide a concurrent race and return a neutral conflict.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="登录账号或昵称标签刚刚被其他人使用，请更换后重试",
        ) from None

    if verification_token:
        try:
            await _deliver_security_link(
                recipient=email, token=verification_token, purpose=VERIFY_EMAIL
            )
        except EmailDeliveryUnavailable:
            logger.exception("could not deliver registration verification email")

    logger.info(
        "registered login account %s with display id %s#%s -> profile %s",
        username,
        nickname,
        payload.tag,
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


@router.put("/email", response_model=AccountInfo, status_code=status.HTTP_202_ACCEPTED)
async def set_recovery_email(
    payload: EmailAddressRequest,
    caller: CallerIdentity = Depends(require_caller),
    db: AsyncSession = Depends(get_db),
) -> AccountInfo:
    """Attach an email and send proof-of-ownership before enabling recovery."""

    email = str(payload.email).strip()
    normalized = _normalise_email(payload.email)
    duplicate = (
        await db.execute(
            select(AccountEmail.account_id).where(
                AccountEmail.normalized_email == normalized,
                AccountEmail.account_id != caller.account.id,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="这个邮箱已绑定其他账号",
        )

    current = (
        await db.execute(
            select(AccountEmail).where(AccountEmail.account_id == caller.account.id)
        )
    ).scalar_one_or_none()
    changed = current is None or current.normalized_email != normalized
    if current is None:
        current = AccountEmail(
            account_id=caller.account.id,
            email=email,
            normalized_email=normalized,
        )
        db.add(current)
    elif changed:
        current.email = email
        current.normalized_email = normalized
        current.verified_at = None

    if current.verified_at is None and email_delivery_configured():
        raw = await _issue_email_token(
            db,
            account_id=caller.account.id,
            purpose=VERIFY_EMAIL,
            ttl_seconds=get_settings().email_verification_ttl_seconds,
            force=changed,
        )
    else:
        raw = None
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="这个邮箱刚刚被其他账号绑定",
        ) from None

    if raw:
        try:
            await _deliver_security_link(
                recipient=email, token=raw, purpose=VERIFY_EMAIL
            )
        except EmailDeliveryUnavailable as exc:
            logger.exception("could not deliver email verification")
            # The address is still retained so a legacy account is not locked
            # behind this mandatory first-login step merely because the mail
            # provider is having an outage. It remains unverified and cannot
            # be used for password reset until a resend succeeds.
    return await _account_info(db, caller.account)


@router.post(
    "/email/verification/resend",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_email_verification(
    caller: CallerIdentity = Depends(require_caller),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    account_email = (
        await db.execute(
            select(AccountEmail).where(AccountEmail.account_id == caller.account.id)
        )
    ).scalar_one_or_none()
    if account_email is None:
        raise HTTPException(status_code=400, detail="请先填写邮箱")
    if account_email.verified_at is not None:
        return MessageResponse(message="邮箱已经验证")
    if not email_delivery_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="邮件服务尚未配置，请联系管理员",
        )
    raw = await _issue_email_token(
        db,
        account_id=caller.account.id,
        purpose=VERIFY_EMAIL,
        ttl_seconds=get_settings().email_verification_ttl_seconds,
    )
    await db.commit()
    if raw:
        try:
            await _deliver_security_link(
                recipient=account_email.email, token=raw, purpose=VERIFY_EMAIL
            )
        except EmailDeliveryUnavailable as exc:
            logger.exception("could not resend email verification")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="验证邮件暂时无法发送，请稍后重试",
            ) from exc
    return MessageResponse(message="如果距离上次发送已超过一分钟，验证邮件已发出")


@router.post("/email/verify", response_model=MessageResponse)
async def verify_email(
    payload: EmailTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    now = _utcnow()
    token_row = (
        await db.execute(
            select(AccountEmailToken).where(
                AccountEmailToken.token_hash == hash_token(payload.token),
                AccountEmailToken.purpose == VERIFY_EMAIL,
                AccountEmailToken.consumed_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if token_row is None or token_row.expires_at <= now:
        raise HTTPException(status_code=400, detail="验证链接已失效，请重新发送")
    account_email = (
        await db.execute(
            select(AccountEmail).where(AccountEmail.account_id == token_row.account_id)
        )
    ).scalar_one_or_none()
    if account_email is None:
        raise HTTPException(status_code=400, detail="验证链接已失效，请重新发送")
    account_email.verified_at = now
    token_row.consumed_at = now
    await db.commit()
    return MessageResponse(message="邮箱验证成功，现在可以用于找回密码")


@router.post(
    "/password/forgot",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Send a reset link without revealing whether the address exists."""

    neutral = MessageResponse(
        message="如果该邮箱已完成验证，我们会发送一封密码重置邮件"
    )
    if not email_delivery_configured():
        logger.error("password reset requested while SMTP is not configured")
        return neutral
    account_email = (
        await db.execute(
            select(AccountEmail).where(
                AccountEmail.normalized_email == _normalise_email(payload.email),
                AccountEmail.verified_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if account_email is None:
        return neutral
    raw = await _issue_email_token(
        db,
        account_id=account_email.account_id,
        purpose=PASSWORD_RESET,
        ttl_seconds=get_settings().password_reset_ttl_seconds,
    )
    await db.commit()
    if raw:
        try:
            await _deliver_security_link(
                recipient=account_email.email, token=raw, purpose=PASSWORD_RESET
            )
        except EmailDeliveryUnavailable:
            # Public response remains deliberately identical. Operators get a
            # loud log without turning this endpoint into an email/account
            # existence oracle for the caller.
            logger.exception("could not deliver password reset email")
    return neutral


@router.post("/password/reset", response_model=MessageResponse)
async def reset_forgotten_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    now = _utcnow()
    token_row = (
        await db.execute(
            select(AccountEmailToken).where(
                AccountEmailToken.token_hash == hash_token(payload.token),
                AccountEmailToken.purpose == PASSWORD_RESET,
                AccountEmailToken.consumed_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if token_row is None or token_row.expires_at <= now:
        raise HTTPException(status_code=400, detail="重置链接已失效，请重新申请")
    account = (
        await db.execute(
            select(UserAccount).where(UserAccount.id == token_row.account_id)
        )
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=400, detail="重置链接已失效，请重新申请")

    account.password_hash = hash_password(payload.new_password)
    token_row.consumed_at = now
    for session in list(account.sessions):
        await db.delete(session)
    # Invalidate every other outstanding reset link too. A password change is
    # the security boundary; an older email must not be able to change it back.
    outstanding = (
        await db.execute(
            select(AccountEmailToken).where(
                AccountEmailToken.account_id == account.id,
                AccountEmailToken.purpose == PASSWORD_RESET,
                AccountEmailToken.consumed_at.is_(None),
            )
        )
    ).scalars().all()
    for other in outstanding:
        other.consumed_at = now
    await db.commit()
    return MessageResponse(message="密码已更新，请使用新密码登录")


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
