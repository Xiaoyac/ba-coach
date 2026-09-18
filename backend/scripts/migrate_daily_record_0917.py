"""Explicit MySQL-only daily-record migration. Dry-run by default, no data import.

Run before deploying the updated ORM. Four optional scores become nullable;
scale_version defaults to 1 so historical 0-10 records remain distinguishable.
New code explicitly writes version 2 (0-5). Never rewrites historical scores.
"""
import argparse
import asyncio

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings


def migration_plan(entry, activity):
    """Validate observed column metadata before preparing additive/relaxing DDL."""
    if not {'subject_id', 'recorded_on', 'completion_rate', 'overall_mood'}.issubset(entry):
        raise RuntimeError('Unexpected assessment schema')
    optional = ['achievement', 'connection', 'enjoyment', 'importance']
    if not {'emotion', 'activity', 'time_slot', *optional}.issubset(activity):
        raise RuntimeError('Unexpected activity schema')
    ddl = []
    if 'scale_version' not in entry:
        ddl.append('ALTER TABLE assessment_entries ADD COLUMN scale_version INTEGER NOT NULL DEFAULT 1, ALGORITHM=INSTANT')
    elif 'INT' not in str(entry['scale_version']['type']).upper() or entry['scale_version']['nullable']:
        raise RuntimeError('Unexpected scale_version definition')
    changes = []
    for name in optional:
        if str(activity[name]['type']).upper() not in {'INTEGER', 'INT'}:
            raise RuntimeError('Unexpected optional score type')
        if not activity[name]['nullable']:
            changes.append(f'MODIFY COLUMN {name} INTEGER NULL')
    if changes:
        ddl.append('ALTER TABLE activity_logs '+', '.join(changes)+', ALGORITHM=INPLACE, LOCK=NONE')
    return ddl


async def migrate(url, *, expected_database, apply=False):
    engine = create_async_engine(url)
    try:
        if engine.url.database != expected_database or engine.dialect.name != "mysql":
            raise RuntimeError("Expected the explicitly named MySQL database; no changes made")
        async with engine.begin() as conn:
            entry = await conn.run_sync(lambda c: {x['name']:x for x in inspect(c).get_columns('assessment_entries')})
            activity = await conn.run_sync(lambda c: {x['name']:x for x in inspect(c).get_columns('activity_logs')})
            ddl = migration_plan(entry, activity)
            for statement in ddl:
                print('PLAN '+statement)
                if apply:
                    await conn.execute(text('SET SESSION lock_wait_timeout=5'))
                    await conn.execute(text(statement))
            print('APPLIED' if apply else 'DRY RUN; no changes')
            print('No test data imports, row deletions, score conversions or historical backfills.')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-database', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    asyncio.run(migrate(get_settings().database_url, expected_database=args.expected_database, apply=args.apply))
