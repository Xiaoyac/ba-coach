"""Which model answers a subject: admins keep their own pick, others the global one."""
from sqlalchemy import select

from .config import get_settings
from .models import AccountSettings, GlobalModelSetting, UserAccount

GLOBAL_ID = 1


async def global_provider(db) -> str:
    stored = await db.scalar(select(GlobalModelSetting.provider).where(GlobalModelSetting.id == GLOBAL_ID))
    return stored or get_settings().default_provider


async def subject_provider(db, subject_id: str | None) -> str:
    if not subject_id:
        return get_settings().default_provider
    row = (await db.execute(select(AccountSettings.role, AccountSettings.preferred_provider)
        .join(UserAccount, UserAccount.id == AccountSettings.account_id)
        .where(UserAccount.profile_uuid == subject_id))).one_or_none()
    if row is not None and row.role == "admin":
        return row.preferred_provider or get_settings().default_provider
    return await global_provider(db)


async def deploy_global_provider(db, *, provider: str, account_id: int) -> None:
    row = await db.get(GlobalModelSetting, GLOBAL_ID)
    if row is None:
        db.add(GlobalModelSetting(id=GLOBAL_ID, provider=provider, updated_by_account_id=account_id))
    else:
        row.provider, row.updated_by_account_id = provider, account_id
    await db.commit()
