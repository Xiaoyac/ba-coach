"""Caller identity — resolved from a bearer token, not asserted by the client.

This module used to trust an `X-Subject-Id` header: an assertion anyone could
forge, adequate only for a single-user local tool. It now resolves identity
from an `Authorization: Bearer <token>` session minted by `routes/auth.py`.

The important design point is what `subject_id` *becomes*. Rather than
inventing a third identifier, it is now `user_profile.uuid` — the same value
every clinical table in `models_business.py` keys on as `user_id`. So:

    Authorization: Bearer …  ->  auth_sessions  ->  user_accounts
                                                          |
                                              profile_uuid = user_profile.uuid
                                                          |
                                          = subject_id everywhere downstream

Every subject-scoped feature written before accounts existed (the sidebar, the
daily assessment) keeps working untouched — it still asks for `subject_id`,
that value is simply now authenticated *and* the same key the business schema
already uses, so conversations and clinical records line up on one identifier
instead of two.

Three flavours:

    require_caller       the full identity (account + session). For /auth.
    require_subject_id   401 if unauthenticated. The sidebar and assessment
                         have no meaning for an anonymous caller.
    optional_subject_id  None if unauthenticated, so chat still answers a
                         caller with no token (curl, /docs) — it just persists
                         nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .db import get_db
from .models import AuthSession, UserAccount, _utcnow

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    # Tells a browser client this is a token problem to fix by signing in
    # again, not a permission problem to report to the user as a dead end.
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class CallerIdentity:
    """An authenticated caller: their account, and the session they used."""

    account: UserAccount
    session: AuthSession

    @property
    def subject_id(self) -> str:
        return self.account.profile_uuid


def _bearer_token(request: Request) -> str | None:
    """Extract the token from `Authorization: Bearer …`, if present and sane."""
    header = request.headers.get("Authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def _resolve(request: Request, db: AsyncSession) -> CallerIdentity | None:
    """Token -> identity, or None. Expired sessions resolve to None."""
    from .security import hash_token  # local import keeps argon2 off the import path

    token = _bearer_token(request)
    if token is None:
        return None

    session = (
        await db.execute(
            select(AuthSession)
            .where(AuthSession.token_hash == hash_token(token))
            # The account (and its own sessions, for "log out everywhere") are
            # needed by the auth routes; loading them here keeps those handlers
            # from issuing a second query per request.
            .options(selectinload(AuthSession.account).selectinload(UserAccount.sessions))
            .options(selectinload(AuthSession.account).selectinload(UserAccount.settings))
        )
    ).scalar_one_or_none()

    if session is None:
        return None

    if session.expires_at <= _utcnow():
        # Expired sessions are dropped on sight rather than left to accumulate
        # — this is the only moment we are certain a given row is dead, and
        # there is no scheduled job to sweep them.
        await db.delete(session)
        await db.commit()
        return None

    return CallerIdentity(account=session.account, session=session)


async def require_caller(
    request: Request, db: AsyncSession = Depends(get_db)
) -> CallerIdentity:
    caller = await _resolve(request, db)
    if caller is None:
        raise _UNAUTHENTICATED
    return caller


async def require_subject_id(
    request: Request, db: AsyncSession = Depends(get_db)
) -> str:
    caller = await _resolve(request, db)
    if caller is None:
        raise _UNAUTHENTICATED
    return caller.subject_id


async def require_admin(
    caller: CallerIdentity = Depends(require_caller),
) -> CallerIdentity:
    """Authenticated administrator, ready for future admin-only routes."""
    if caller.account.settings is None or caller.account.settings.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return caller


async def optional_subject_id(
    request: Request, db: AsyncSession = Depends(get_db)
) -> str | None:
    caller = await _resolve(request, db)
    # An expired logged-in request must not silently become an anonymous chat:
    # the answer would not be stored and would disappear on synchronization.
    if caller is None and request.headers.get("Authorization") is not None:
        raise _UNAUTHENTICATED
    return caller.subject_id if caller else None
