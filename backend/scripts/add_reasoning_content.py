"""Add the nullable reasoning column to the app-owned transcript table.

`Base.metadata.create_all()` intentionally never alters existing tables, so
deployments created before reasoning disclosure need this one idempotent
migration. The seven externally-owned clinical tables are not touched.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            if connection.dialect.name != "mysql":
                raise RuntimeError("This migration is only for the deployed MySQL database")

            exists = await connection.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'conversation_messages'
                      AND COLUMN_NAME = 'reasoning_content'
                    """
                )
            )
            if exists:
                print("conversation_messages.reasoning_content already exists")
                return

            await connection.execute(
                text(
                    "ALTER TABLE conversation_messages "
                    "ADD COLUMN reasoning_content LONGTEXT NULL AFTER content"
                )
            )
            print("added conversation_messages.reasoning_content")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
