"""Single-use invitations. Never expire or restore one after account deletion."""
import secrets

from fastapi import HTTPException
from sqlalchemy import update

from .models import RegistrationInvite, _utcnow


def new_invitation_code() -> str:
    return secrets.token_hex(16).upper()


def normalize_invitation_code(value: str) -> str:
    return value.strip().replace("-", "").upper()


def display_invitation_code(value: str) -> str:
    return "-".join(value[i:i + 8] for i in range(0, len(value), 8))


async def claim_invitation(db, code: str) -> None:
    # A conditional UPDATE is a cross-process database lock/CAS, not a Python
    # pre-check. The winning claim commits with the account; any failure rolls
    # the same transaction back. A second registration can never win this row.
    result = await db.execute(update(RegistrationInvite).where(
        RegistrationInvite.code == code, RegistrationInvite.used_at.is_(None)
    ).values(used_at=_utcnow()))
    if result.rowcount != 1:
        raise HTTPException(status_code=400, detail="邀请码无效或已被使用，请向管理员获取新的邀请码")
