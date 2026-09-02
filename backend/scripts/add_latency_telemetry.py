"""Add app-owned latency telemetry and a mutable conversation revision.

Idempotent and MySQL-only.  The seven externally-owned clinical tables are
never touched.  The revision is initialised from each conversation's current
message count so existing clients immediately receive a sensible baseline.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings


async def _column_exists(connection, table: str, column: str) -> bool:
    count = await connection.scalar(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND COLUMN_NAME = :column
            """
        ),
        {"table": table, "column": column},
    )
    return bool(count)


async def _add_column(connection, table: str, column: str, ddl: str) -> bool:
    if await _column_exists(connection, table, column):
        print(f"{table}.{column} already exists")
        return False
    # table/column/ddl are source-controlled constants below, never user input.
    await connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    print(f"added {table}.{column}")
    return True


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            if connection.dialect.name != "mysql":
                raise RuntimeError("This migration is only for the deployed MySQL database")

            revision_added = await _add_column(
                connection,
                "conversations",
                "revision",
                "INT NOT NULL DEFAULT 0 AFTER pinned",
            )
            if revision_added:
                await connection.execute(
                    text(
                        """
                        UPDATE conversations AS c
                        LEFT JOIN (
                            SELECT conversation_id, COUNT(*) AS message_count
                            FROM conversation_messages
                            GROUP BY conversation_id
                        ) AS counts ON counts.conversation_id = c.id
                        SET c.revision = COALESCE(counts.message_count, 0)
                        """
                    )
                )
                print("initialised conversations.revision from message counts")

            columns = [
                ("risk_gate_duration_ms", "INT NULL AFTER router_model_name"),
                ("time_to_first_reasoning_token_ms", "INT NULL AFTER risk_gate_duration_ms"),
                ("time_to_first_content_token_ms", "INT NULL AFTER time_to_first_reasoning_token_ms"),
                ("main_generation_duration_ms", "INT NULL AFTER time_to_first_content_token_ms"),
                ("router_duration_ms", "INT NULL AFTER main_generation_duration_ms"),
                ("input_tokens", "INT NULL AFTER router_duration_ms"),
                ("output_tokens", "INT NULL AFTER input_tokens"),
                ("reasoning_tokens", "INT NULL AFTER output_tokens"),
                ("provider_request_id", "VARCHAR(128) NULL AFTER reasoning_tokens"),
                ("finish_reason", "VARCHAR(32) NULL AFTER provider_request_id"),
                ("error_code", "VARCHAR(64) NULL AFTER finish_reason"),
                ("prompt_version", "VARCHAR(64) NULL AFTER error_code"),
            ]
            for name, ddl in columns:
                await _add_column(connection, "conversation_messages", name, ddl)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
