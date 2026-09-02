"""Add persisted Router Agent reasoning to the app-owned transcript table.

Idempotent and MySQL-only. The seven externally-owned clinical tables are not
touched. Run after deploying code that exposes the module-routing thought panel.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings


async def _column_exists(connection, column_name: str) -> bool:
    count = await connection.scalar(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'conversation_messages'
              AND COLUMN_NAME = :column_name
            """
        ),
        {"column_name": column_name},
    )
    return bool(count)


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            if connection.dialect.name != "mysql":
                raise RuntimeError("This migration is only for the deployed MySQL database")

            if await _column_exists(connection, "routing_reasoning_content"):
                print("conversation_messages.routing_reasoning_content already exists")
            else:
                await connection.execute(
                    text(
                        "ALTER TABLE conversation_messages "
                        "ADD COLUMN routing_reasoning_content LONGTEXT NULL AFTER model_name"
                    )
                )
                print("added conversation_messages.routing_reasoning_content")

            if await _column_exists(connection, "router_model_name"):
                print("conversation_messages.router_model_name already exists")
            else:
                await connection.execute(
                    text(
                        "ALTER TABLE conversation_messages "
                        "ADD COLUMN router_model_name VARCHAR(128) NULL "
                        "AFTER routing_reasoning_content"
                    )
                )
                print("added conversation_messages.router_model_name")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
