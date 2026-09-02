r"""One-time migration to independent login accounts + chosen display tags.

Dry-run (default)::

    .venv\Scripts\python.exe scripts\migrate_identity_v2.py

Apply the user-authorized cleanup::

    .venv\Scripts\python.exe scripts\migrate_identity_v2.py --apply

The migration preserves the admin credentials and profile, removes its old
random display tag, and deletes every other login account. Clinical profiles,
conversations, assessments, and module records are deliberately untouched.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import dispose_db, get_sessionmaker, init_db  # noqa: E402
from app.models import AccountHandle, AccountSettings, UserAccount  # noqa: E402


def _looks_like_legacy_admin(account: UserAccount, handle: AccountHandle | None) -> bool:
    internal = account.username.strip().casefold()
    old_handle = handle.normalized_base if handle else None
    return (
        internal == "admin"
        or (
            internal.startswith("admin#")
            and len(internal) == 11
            and internal[6:].isascii()
            and internal[6:].isdigit()
        )
        or old_handle == "admin"
    )


async def migrate(*, apply: bool) -> int:
    await init_db()
    async with get_sessionmaker()() as db:
        accounts = (await db.execute(select(UserAccount))).scalars().unique().all()
        handles = {
            row.account_id: row
            for row in (await db.execute(select(AccountHandle))).scalars().all()
        }
        admins = [
            account
            for account in accounts
            if _looks_like_legacy_admin(account, handles.get(account.id))
        ]
        if len(admins) != 1:
            print(
                f"安全检查失败：预期恰好一个 admin，实际找到 {len(admins)} 个；未修改数据库。",
                file=sys.stderr,
            )
            return 1

        admin = admins[0]
        removable = [account for account in accounts if account.id != admin.id]
        print(
            f"检查结果：共 {len(accounts)} 个登录账号；将保留 admin，移除 {len(removable)} 个。"
        )
        print("心理档案、对话、评估和模块记录不会删除。")
        if not apply:
            print("当前为预演，没有修改数据库。确认后使用 --apply。")
            return 0

        old_admin_handle = handles.get(admin.id)
        if old_admin_handle is not None:
            await db.delete(old_admin_handle)
        admin.username = "admin"

        settings = (
            await db.execute(
                select(AccountSettings).where(AccountSettings.account_id == admin.id)
            )
        ).scalar_one_or_none()
        if settings is None:
            db.add(AccountSettings(account_id=admin.id, role="admin"))
        else:
            settings.role = "admin"

        for account in removable:
            await db.delete(account)
        await db.commit()

        remaining = (await db.execute(select(UserAccount))).scalars().all()
        remaining_handles = (await db.execute(select(AccountHandle))).scalars().all()
        if (
            len(remaining) != 1
            or remaining[0].username != "admin"
            or remaining_handles
        ):
            raise RuntimeError("迁移后验证失败")
        print("迁移完成：仅保留登录账号 admin，所有旧随机标签已移除。")
        return 0


async def main() -> int:
    try:
        return await migrate(apply="--apply" in sys.argv[1:])
    finally:
        await dispose_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
