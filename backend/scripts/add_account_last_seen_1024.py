"""Add user_accounts.last_seen_at for the admin 最近活动 time.

Idempotent and MySQL-only. Run before deploying the code that writes it.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db import get_engine


async def main() -> None:
    engine = get_engine()
    try:
        async with engine.begin() as connection:
            if connection.dialect.name != "mysql":
                raise RuntimeError("This migration is only for the deployed MySQL database")
            exists = await connection.scalar(text(
                "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'user_accounts' AND COLUMN_NAME = 'last_seen_at'"))
            if exists:
                print("user_accounts.last_seen_at already exists")
                return
            await connection.execute(text(
                "ALTER TABLE user_accounts ADD COLUMN last_seen_at DATETIME NULL AFTER last_login_at"))
            print("added user_accounts.last_seen_at")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
