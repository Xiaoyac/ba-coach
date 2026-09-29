"""Add daily-record revisions. Dry-run by default; run before deploying the ORM.

No score conversions, date changes, deletions, or historical snapshot backfill.
The first edit saves a baseline snapshot of an older record transactionally.
"""
import argparse
import asyncio
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateTable
from app.config import get_settings
from app.models import AssessmentRevision


async def migrate(url, *, expected_database, apply=False):
    engine = create_async_engine(url)
    try:
        if engine.url.database != expected_database or engine.dialect.name not in {'mysql', 'sqlite'}:
            raise RuntimeError('Unexpected database; no changes made')
        async with engine.begin() as conn:
            cols = await conn.run_sync(lambda c: {x['name']: x for x in inspect(c).get_columns('assessment_entries')})
            if not {'id', 'subject_id', 'recorded_on', 'scale_version'}.issubset(cols):
                raise RuntimeError('Unexpected assessment schema')
            if 'revision_no' not in cols:
                sql = 'ALTER TABLE assessment_entries ADD COLUMN revision_no INTEGER NOT NULL DEFAULT 1'
                if engine.dialect.name == 'mysql':
                    sql += ', ALGORITHM=INSTANT'
                print('PLAN ' + sql)
                if apply:
                    if engine.dialect.name == 'mysql':
                        await conn.execute(text('SET SESSION lock_wait_timeout=5'))
                    await conn.execute(text(sql))
            elif cols['revision_no']['nullable'] or 'INT' not in str(cols['revision_no']['type']).upper():
                raise RuntimeError('Unexpected revision_no definition')
            exists = await conn.run_sync(lambda c: inspect(c).has_table('assessment_revisions'))
            if not exists:
                print('PLAN ' + str(CreateTable(AssessmentRevision.__table__).compile(dialect=conn.dialect)))
                if apply:
                    await conn.run_sync(lambda c: AssessmentRevision.__table__.create(c, checkfirst=True))
            else:
                names = await conn.run_sync(lambda c: {x['name'] for x in inspect(c).get_columns('assessment_revisions')})
                if names != {'id', 'entry_id', 'revision_no', 'snapshot', 'saved_at'}:
                    raise RuntimeError('Unexpected revision schema')
            print('APPLIED' if apply else 'DRY RUN; no changes')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-database', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    asyncio.run(migrate(get_settings().database_url, expected_database=args.expected_database, apply=args.apply))
