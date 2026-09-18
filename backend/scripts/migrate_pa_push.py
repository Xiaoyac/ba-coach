"""Add empty opt-in push tables. Dry-run unless --apply, exact target required."""
import argparse
import asyncio
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateTable, CreateIndex
from app.config import get_settings
from app.push_schema import metadata


async def migrate(url, expected_database, apply=False):
    engine = create_async_engine(url)
    try:
        if engine.url.database != expected_database: raise ValueError('Database target mismatch')
        if engine.dialect.name not in {'sqlite', 'mysql'}: raise ValueError('Unsupported database')
        async with engine.begin() as conn:
            existing = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
            if not {'pa_goals', 'module_two_record', 'pa_cycles', 'auth_sessions'}.issubset(existing):
                raise ValueError('Existing V2 business and auth schema required')
            for table in metadata.sorted_tables:
                if table.name in existing:
                    columns = await conn.run_sync(lambda c: {r['name'] for r in inspect(c).get_columns(table.name)})
                    if columns != set(table.c.keys()): raise ValueError(f'Unexpected schema: {table.name}')
                    print(f'EXISTS {table.name}'); continue
                print(CreateTable(table).compile(dialect=conn.dialect))
                for index in table.indexes: print(CreateIndex(index).compile(dialect=conn.dialect))
                if apply: await conn.run_sync(table.create)
            print('APPLIED: empty tables only' if apply else 'DRY RUN: no schema/data changes')
    finally: await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-database', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    asyncio.run(migrate(get_settings().database_url, args.expected_database, args.apply))
