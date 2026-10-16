"""Add immutable prompt audit tables/triggers and baseline current effective text.

Run once with prompt writers stopped. No historical versions are fabricated;
the baseline retains the previous editor/time, and all later writes are audited.
"""
import asyncio
from app.db import get_engine, dispose_db, get_sessionmaker
from app.prompt_history import initialize_history, sync_source_defaults

async def main():
    try:
        await initialize_history(get_engine())
        async with get_sessionmaker()() as db:
            await sync_source_defaults(db)
        print('Prompt history tables, baselines and audit triggers ready')
    finally:
        await dispose_db()

if __name__ == '__main__':
    asyncio.run(main())
