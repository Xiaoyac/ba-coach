"""Remove the retired profile choice from existing V1/V2 databases.

Read-only by default. Deploy the new application and drain old workers before
--apply. A new, private backup file is required; --restore supports rollback.
Only the two allow-listed columns are touched, never the shared profile tables.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from sqlalchemy import MetaData, Table, inspect, select, text
from sqlalchemy.schema import CreateColumn


TARGETS = {"user_profile": "communication_preference",
           "user_preferences": "communication_style"}


def migrate(conn, *, apply=False, backup: Path | None = None):
    targets = []
    for name, field in TARGETS.items():
        if not inspect(conn).has_table(name):
            continue
        table = Table(name, MetaData(), autoload_with=conn)
        if field in table.c:
            if not table.primary_key.columns:
                raise RuntimeError(f"Missing primary key: {name}")
            targets.append((table, field))
    result = {"changed": False, "columns": [f"{t.name}.{f}" for t, f in targets]}
    if not apply or not targets:
        return result
    if backup is None:
        raise ValueError("--backup is required")
    package = {"version": 1, "dialect": conn.dialect.name, "columns": []}
    for table, field in targets:
        keys = list(table.primary_key.columns)
        rows = [dict(r) for r in conn.execute(select(*keys, table.c[field])).mappings()]
        package["columns"].append({"table": table.name, "column": field,
            "primary_key": [c.name for c in keys], "rows": rows,
            "definition": str(CreateColumn(table.c[field]).compile(dialect=conn.dialect))})
    # Never overwrite a previous backup, and never print removed user values.
    with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        json.dump(package, out, ensure_ascii=False)
        out.flush()
        os.fsync(out.fileno())
    quote = conn.dialect.identifier_preparer.quote
    for table, field in targets:
        conn.execute(text(f"ALTER TABLE {quote(table.name)} DROP COLUMN {quote(field)}"))
        if field in {c["name"] for c in inspect(conn).get_columns(table.name)}:
            raise RuntimeError(f"Column still exists: {table.name}.{field}")
    return {**result, "changed": True}


def restore(conn, backup: Path):
    package = json.loads(backup.read_text())
    if package["version"] != 1 or package["dialect"] != conn.dialect.name:
        raise ValueError("Incompatible backup")
    # Backup files are trusted local migration artifacts, not user uploads.
    for entry in package["columns"]:
        if TARGETS.get(entry["table"]) != entry["column"]:
            raise ValueError("Unexpected backup column")
        if entry["column"] in {c["name"] for c in inspect(conn).get_columns(entry["table"])}:
            raise RuntimeError("Refusing to overwrite an existing column")
    quote = conn.dialect.identifier_preparer.quote
    for entry in package["columns"]:
        conn.execute(text(f"ALTER TABLE {quote(entry['table'])} ADD COLUMN {entry['definition']}"))
        table = Table(entry["table"], MetaData(), autoload_with=conn)
        for row in entry["rows"]:
            statement = table.update()
            for key in entry["primary_key"]:
                statement = statement.where(table.c[key] == row[key])
            conn.execute(statement.values({entry["column"]: row[entry["column"]]}))
    return {"restored_columns": len(package["columns"])}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true")
    group.add_argument("--restore", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if (args.apply or args.restore) and args.backup is None:
        parser.error("--backup is required for apply/restore")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.db import get_engine, dispose_db
    try:
        async with get_engine().begin() as conn:
            result = await conn.run_sync(lambda c: restore(c, args.backup) if args.restore
                else migrate(c, apply=args.apply, backup=args.backup))
        print(json.dumps(result))
    finally:
        await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
