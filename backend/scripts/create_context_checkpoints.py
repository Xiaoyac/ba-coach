"""Add the context-summary checkpoint sidecar; read-only unless --apply.

V2 production disables automatic schema maintenance. This additive migration
does not alter conversations, clinical data, existing reply settings or prompts.

PYTHONPATH=. python scripts/create_context_checkpoints.py --expected-database NAME
PYTHONPATH=. python scripts/create_context_checkpoints.py --expected-database NAME --apply
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateTable

from app.models import ConversationContextCheckpoint


def _plan(connection):
    inspector = inspect(connection)
    table = ConversationContextCheckpoint.__table__
    if not inspector.has_table("conversations"):
        raise RuntimeError("Expected the existing conversations table")
    if not inspector.has_table(table.name):
        return [str(CreateTable(table).compile(dialect=connection.dialect)).strip()]
    columns = {column["name"]: column for column in inspector.get_columns(table.name)}
    for expected in table.columns:
        actual = columns.get(expected.name)
        if (actual is None or actual["nullable"] != expected.nullable
                or actual["type"].compile(dialect=connection.dialect)
                != expected.type.compile(dialect=connection.dialect)):
            raise RuntimeError(f"Existing {table.name}.{expected.name} differs from expected schema")
    if inspector.get_pk_constraint(table.name)["constrained_columns"] != ["conversation_id"]:
        raise RuntimeError("Context checkpoint primary key differs from expected schema")
    if not any(
        item["constrained_columns"] == ["conversation_id"]
        and item["referred_table"] == "conversations"
        and item["referred_columns"] == ["id"]
        and (item.get("options") or {}).get("ondelete", "").upper() == "CASCADE"
        for item in inspector.get_foreign_keys(table.name)
    ):
        raise RuntimeError("Context checkpoint foreign key must cascade on conversation deletion")
    return []


async def migrate(url, *, expected_database, apply=False):
    engine = create_async_engine(url)
    try:
        if engine.url.database != expected_database:
            raise RuntimeError("Database name does not match explicit expected target")
        if engine.dialect.name not in {"mysql", "sqlite", "postgresql"}:
            raise RuntimeError("Unsupported database dialect")
        async with engine.begin() as connection:
            plan = await connection.run_sync(_plan)
            for statement in plan:
                print(statement)
            if apply and plan:
                await connection.run_sync(lambda c: ConversationContextCheckpoint.__table__.create(c, checkfirst=True))
                await connection.run_sync(_plan)
            print("APPLIED additive context checkpoint table; no existing data changed" if apply
                  else "READ ONLY: no DDL/data changes")
            return plan
    finally:
        await engine.dispose()


if __name__ == "__main__":
    from app.config import get_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(migrate(get_settings().database_url,
                       expected_database=args.expected_database, apply=args.apply))
