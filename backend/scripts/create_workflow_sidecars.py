"""Create only the app-owned workflow sidecar tables, idempotently.

This script deliberately does not call ``BizBase.metadata.create_all`` and
does not issue ALTER/DROP statements.  The seven externally-owned business
tables are therefore outside its possible write set.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import dispose_db, get_engine  # noqa: E402
from app.models import Base  # noqa: E402


TABLES = (
    "conversation_module_progress",
    "pa_cycles",
    "clinical_record_cycle_links",
    "ai_execution_events",
)


async def main() -> None:
    engine = get_engine()
    async with engine.begin() as connection:
        for name in TABLES:
            table = Base.metadata.tables[name]
            await connection.run_sync(lambda sync_connection, t=table: t.create(sync_connection, checkfirst=True))
            print(f"ready: {name}")
    await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
