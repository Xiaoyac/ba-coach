"""Administrator-only generation and retrieval of single-use invitations."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..identity import CallerIdentity, require_admin
from ..invitations import display_invitation_code, new_invitation_code
from ..models import RegistrationInvite, UserAccount

router = APIRouter(prefix="/admin/invitations", tags=["admin-invitations"])


class InviteCreate(BaseModel):
    count: int = Field(default=1, ge=1, le=20, strict=True)


class InviteItem(BaseModel):
    id: int
    code: str
    created_at: datetime
    used_at: datetime | None
    used_by_username: str | None = None


class InviteList(BaseModel):
    invitations: list[InviteItem]
    has_more: bool = False


def item(invite, username=None):
    return InviteItem(id=invite.id, code=display_invitation_code(invite.code),
                     created_at=invite.created_at, used_at=invite.used_at, used_by_username=username)


@router.post("", response_model=InviteList, status_code=201)
async def create_invitations(payload: InviteCreate, response: Response,
    caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    invitations = [RegistrationInvite(code=new_invitation_code(), created_by_account_id=caller.account.id)
                   for _ in range(payload.count)]
    db.add_all(invitations)
    await db.commit()
    return InviteList(invitations=[item(invite) for invite in invitations])


@router.get("", response_model=InviteList)
async def list_invitations(response: Response, used: bool = False,
    offset: int = Query(default=0, ge=0),
    caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    rows = (await db.execute(select(RegistrationInvite, UserAccount.username)
        .outerjoin(UserAccount, UserAccount.id == RegistrationInvite.used_by_account_id)
        .where(RegistrationInvite.used_at.is_not(None) if used else RegistrationInvite.used_at.is_(None))
        .order_by(RegistrationInvite.id.desc()).offset(offset).limit(51))).all()
    return InviteList(invitations=[item(invite, username) for invite, username in rows[:50]], has_more=len(rows) > 50)
