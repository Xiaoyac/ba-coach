"""Preview/apply the one additive mediator preference table; no data backfill.

Run from backend with production environment loaded:
    .venv/bin/python scripts/create_mediator_node_settings.py [--apply]
V2 production disables startup create_all, so run this before activating code.
"""
import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect
from app.db import get_engine, dispose_db
from app.models import ConversationMediatorNodeSettings


async def main(apply=False):
    table = ConversationMediatorNodeSettings.__table__
    engine = get_engine()
    async with engine.begin() as conn:
        existed = await conn.run_sync(lambda c: inspect(c).has_table(table.name))
        if apply:
            await conn.run_sync(lambda c: table.create(c, checkfirst=True))
        if existed or apply:
            columns = await conn.run_sync(lambda c: inspect(c).get_columns(table.name))
            assert {c['name'] for c in columns} == {'conversation_id', 'enabled'}
            assert all(not c['nullable'] for c in columns)
        print(f'{table.name}: ' + ('verified' if existed or apply else 'missing; use --apply'))
    await dispose_db()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args().apply))
