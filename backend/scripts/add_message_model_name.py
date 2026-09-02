"""Add per-message model attribution to the app-owned transcript table.

Idempotent and MySQL-only. The seven externally-owned clinical tables are not
touched.
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
                      AND COLUMN_NAME = 'model_name'
                    """
                )
            )
            if exists:
                print("conversation_messages.model_name already exists")
                return

            await connection.execute(
                text(
                    "ALTER TABLE conversation_messages "
                    "ADD COLUMN model_name VARCHAR(128) NULL AFTER reasoning_content"
                )
            )
            print("added conversation_messages.model_name")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
