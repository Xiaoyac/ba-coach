"""Add the app-owned conversation_shares table; read-only unless --apply.

V2 deliberately disables startup schema maintenance. Run this against the
explicitly named target before deploying sharing routes. Existing transcripts
and business tables are never rewritten or altered by this migration.

PYTHONPATH=. python scripts/create_conversation_shares.py --expected-database NAME
PYTHONPATH=. python scripts/create_conversation_shares.py --expected-database NAME --apply
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models import ConversationShare


def _plan(connection):
    inspector = inspect(connection)
    table = ConversationShare.__table__
    if not inspector.has_table("conversations"):
        raise RuntimeError("Expected the existing conversations table; initialize the application database first")
    if inspector.has_table(table.name):
        columns = {column["name"]: column for column in inspector.get_columns(table.name)}
        for expected in table.columns:
            actual = columns.get(expected.name)
            if (actual is None or actual["nullable"] != expected.nullable
                    or actual["type"].compile(dialect=connection.dialect)
                    != expected.type.compile(dialect=connection.dialect)):
                raise RuntimeError(f"Existing conversation_shares.{expected.name} differs from expected schema")
        if inspector.get_pk_constraint(table.name)["constrained_columns"] != ["id"]:
            raise RuntimeError("Existing conversation_shares primary key differs from expected schema")
        indexes = inspector.get_indexes(table.name)
        uniques = inspector.get_unique_constraints(table.name)
        if not any(item["column_names"] == ["token_digest"] for item in uniques) and not any(
            item["unique"] and item["column_names"] == ["token_digest"] for item in indexes
        ):
            raise RuntimeError("Existing conversation_shares token digest must be unique")
        if not any(
            item["constrained_columns"] == ["conversation_id"]
            and item["referred_table"] == "conversations"
            and item["referred_columns"] == ["id"]
            and (item.get("options") or {}).get("ondelete", "").upper() == "CASCADE"
            for item in inspector.get_foreign_keys(table.name)
        ):
            raise RuntimeError("Existing conversation_shares foreign key differs from expected schema")
        return []
    return [str(CreateTable(table).compile(dialect=connection.dialect)).strip(), *[
        str(CreateIndex(index).compile(dialect=connection.dialect))
        for index in sorted(table.indexes, key=lambda item: item.name)
    ]]


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
                # A single app-owned table, not metadata.create_all or any ALTER.
                await connection.run_sync(lambda c: ConversationShare.__table__.create(c, checkfirst=True))
            print("APPLIED additive share table; no existing data changed" if apply
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
