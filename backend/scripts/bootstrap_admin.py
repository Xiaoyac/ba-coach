r"""Create or promote one administrator account.

Usage from ``backend``::

    $env:PSY_ADMIN_PASSWORD = "..."
    .venv\Scripts\python.exe scripts\bootstrap_admin.py admin

The password is read from an environment variable so it is never committed to
source.  Re-running is idempotent: it promotes the existing account, replaces
its password, and revokes old sessions.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.account_identity import (  # noqa: E402
    find_account_by_username,
    normalize_account,
)
from app.db import dispose_db, get_sessionmaker, init_db  # noqa: E402
from app.models import AccountSettings, AuthSession, UserAccount  # noqa: E402
from app.models_business import UserProfile  # noqa: E402
from app.security import hash_password  # noqa: E402


async def bootstrap(username: str, password: str) -> None:
    await init_db()
    async with get_sessionmaker()() as db:
        entered = username.strip()
        account = await find_account_by_username(db, entered)

        if account is None:
            if not 3 <= len(entered) <= 32 or not entered.isascii() or not entered.isalnum():
                raise ValueError("登录账号须为 3–32 位英文字母或数字。")
            normalised = normalize_account(entered)
            profile = UserProfile(nickname="管理员", current_module="开场")
            db.add(profile)
            await db.flush()
            account = UserAccount(
                username=normalised,
                password_hash=hash_password(password),
                profile_uuid=profile.uuid,
            )
            db.add(account)
            await db.flush()
        else:
            account.password_hash = hash_password(password)

        settings = (
            await db.execute(
                select(AccountSettings).where(AccountSettings.account_id == account.id)
            )
        ).scalar_one_or_none()
        if settings is None:
            settings = AccountSettings(account_id=account.id)
            db.add(settings)
        settings.role = "admin"

        sessions = (
            await db.execute(select(AuthSession).where(AuthSession.account_id == account.id))
        ).scalars().all()
        for session in sessions:
            await db.delete(session)

        await db.commit()
        print(f"管理员登录账号 {account.username!r} 已就绪；已注销 {len(sessions)} 个旧会话。")


async def main() -> int:
    if len(sys.argv) != 2:
        print("用法: bootstrap_admin.py <username>", file=sys.stderr)
        return 2
    password = os.environ.get("PSY_ADMIN_PASSWORD")
    if not password:
        print("缺少 PSY_ADMIN_PASSWORD 环境变量。", file=sys.stderr)
        return 2
    try:
        await bootstrap(sys.argv[1], password)
        return 0
    finally:
        await dispose_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
