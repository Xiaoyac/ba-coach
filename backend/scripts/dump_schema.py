"""Dump the live MySQL schema to schema.json, for `gen_database_doc.py`.

Reads DATABASE_URL straight out of backend/.env rather than through
`app.config`, so it works from any working directory (Settings resolves its
env_file relative to the cwd and would otherwise fall back to SQLite).

    python scripts/dump_schema.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND = Path(__file__).resolve().parent.parent
OUT = BACKEND / "scripts" / "schema.json"

TABLES = """
SELECT TABLE_NAME, TABLE_COMMENT, ENGINE
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME
"""

COLUMNS = """
SELECT TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE,
       COLUMN_KEY, COLUMN_DEFAULT, EXTRA, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_NAME, ORDINAL_POSITION
"""

INDEXES = """
SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
"""


def database_url() -> str:
    env = BACKEND / ".env"
    if not env.exists():
        sys.exit(f"no .env at {env}")
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL"):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
            if not url.startswith("mysql"):
                sys.exit(f"DATABASE_URL is not MySQL, refusing: {url!r}")
            return url
    sys.exit("no DATABASE_URL in .env")


async def main() -> None:
    engine = create_async_engine(database_url())
    async with engine.connect() as conn:
        tables = [dict(r._mapping) for r in await conn.execute(text(TABLES))]
        columns = [dict(r._mapping) for r in await conn.execute(text(COLUMNS))]
        indexes = [dict(r._mapping) for r in await conn.execute(text(INDEXES))]
        # information_schema.TABLE_ROWS is an InnoDB estimate and is routinely
        # wrong by a whole row on small tables — count for real.
        counts = {
            t["TABLE_NAME"]: (
                await conn.execute(text(f"SELECT COUNT(*) FROM `{t['TABLE_NAME']}`"))
            ).scalar()
            for t in tables
        }
    await engine.dispose()

    payload = {
        "tables": tables,
        "columns": columns,
        "indexes": indexes,
        "counts": counts,
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
    print(f"wrote {OUT} — {len(tables)} tables, {len(columns)} columns")


if __name__ == "__main__":
    asyncio.run(main())
