"""Drop the retired daily-record day scores from assessment_entries.

完成程度, 身体活动 and 整体心情 moved to 每日测量. Read-only by default. Deploy
the new application before --apply. A new, private backup file is required;
--restore re-adds the columns (without their CHECK constraints) and values.
Only the allow-listed columns are touched. Revision snapshots are history and
are left as they are.
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


TABLE = "assessment_entries"
COLUMNS = ("completion_rate", "activity_level", "overall_mood")


def migrate(conn, *, apply=False, backup: Path | None = None):
    if not inspect(conn).has_table(TABLE):
        return {"changed": False, "columns": []}
    table = Table(TABLE, MetaData(), autoload_with=conn)
    targets = [name for name in COLUMNS if name in table.c]
    result = {"changed": False, "columns": [f"{TABLE}.{name}" for name in targets]}
    if not apply or not targets:
        return result
    if backup is None:
        raise ValueError("--backup is required")
    if conn.dialect.name != "mysql":
        raise RuntimeError("Only MySQL is supported; local SQLite can keep the unused columns")
    keys = list(table.primary_key.columns)
    if not keys:
        raise RuntimeError(f"Missing primary key: {TABLE}")
    package = {"version": 1, "dialect": conn.dialect.name, "table": TABLE,
               "primary_key": [c.name for c in keys],
               "definitions": {name: str(CreateColumn(table.c[name]).compile(dialect=conn.dialect)) for name in targets},
               "rows": [dict(r) for r in conn.execute(select(*keys, *(table.c[name] for name in targets))).mappings()]}
    # Never overwrite a previous backup, and never print removed user values.
    with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        json.dump(package, out, ensure_ascii=False)
        out.flush()
        os.fsync(out.fileno())
    quote = conn.dialect.identifier_preparer.quote
    checks = {c["name"] for c in inspect(conn).get_check_constraints(TABLE)}
    for name in targets:
        # MySQL refuses to drop a column that a CHECK constraint still uses.
        if f"ck_entry_{name}" in checks:
            conn.execute(text(f"ALTER TABLE {quote(TABLE)} DROP CHECK {quote(f'ck_entry_{name}')}"))
        conn.execute(text(f"ALTER TABLE {quote(TABLE)} DROP COLUMN {quote(name)}"))
    left = {c["name"] for c in inspect(conn).get_columns(TABLE)} & set(targets)
    if left:
        raise RuntimeError(f"Columns still exist: {sorted(left)}")
    return {**result, "changed": True, "rows_backed_up": len(package["rows"])}


def restore(conn, backup: Path):
    package = json.loads(backup.read_text())
    if package["version"] != 1 or package["dialect"] != conn.dialect.name or package["table"] != TABLE:
        raise ValueError("Incompatible backup")
    # Backup files are trusted local migration artifacts, not user uploads.
    names = list(package["definitions"])
    if not set(names) <= set(COLUMNS):
        raise ValueError("Unexpected backup column")
    existing = {c["name"] for c in inspect(conn).get_columns(TABLE)}
    if existing & set(names):
        raise RuntimeError("Refusing to overwrite an existing column")
    quote = conn.dialect.identifier_preparer.quote
    for name in names:
        conn.execute(text(f"ALTER TABLE {quote(TABLE)} ADD COLUMN {package['definitions'][name]}"))
    table = Table(TABLE, MetaData(), autoload_with=conn)
    for row in package["rows"]:
        statement = table.update()
        for key in package["primary_key"]:
            statement = statement.where(table.c[key] == row[key])
        conn.execute(statement.values({name: row[name] for name in names}))
    return {"restored_columns": len(names), "rows": len(package["rows"])}


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
