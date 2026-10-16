"""Add empty PA execution identity/observation links; no historic inference.

Dry-run by default. Rollback removes only these new tables; it never rewrites
existing goals, reviews, observations, or messages. Roll back application code
before removing the tables, and retain a backup of new facts when requested.
"""
import argparse
import asyncio
import json
from sqlalchemy import MetaData, inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateTable
from app.database_v2_schema import metadata
from app.config import get_settings
from scripts.migrate_goal_cards_1003 import mysql_column, reflected_identifier_type

TABLE_NAMES = ('pa_executions', 'pa_activity_attributions')

async def migrate(url, expected_database, *, apply=False):
    engine = create_async_engine(url)
    private = MetaData()
    for original in metadata.sorted_tables:
        original.to_metadata(private)
    try:
        if engine.url.database != expected_database:
            raise ValueError('Configured database does not match --expected-database')
        async with engine.begin() as connection:
            for name in TABLE_NAMES:
                table = private.tables[name]
                if connection.dialect.name == 'mysql':
                    base = await mysql_column(connection, 'pa_goals', 'id')
                    for column in table.columns:
                        if column.primary_key and getattr(column.type, 'length', None) == 36:
                            column.type = reflected_identifier_type(column, base)
                        for fk in column.foreign_keys:
                            parent = fk.column
                            if parent.table.name in TABLE_NAMES:
                                description = base
                            else:
                                description = await mysql_column(connection, parent.table.name, parent.name)
                            column.type = reflected_identifier_type(column, description)
                    table.dialect_options['mysql'].update(charset='utf8mb4', collate='utf8mb4_unicode_ci', engine='InnoDB')
                exists = await connection.run_sync(lambda c: inspect(c).has_table(name))
                if exists:
                    columns = await connection.run_sync(lambda c: inspect(c).get_columns(name))
                    if {c['name'] for c in columns} != set(table.c.keys()):
                        raise RuntimeError('Unexpected existing attribution schema: ' + name)
                    actual = await connection.run_sync(lambda c: inspect(c).get_foreign_keys(name))
                    actual_refs = {(tuple(f['constrained_columns']), f['referred_table'], tuple(f['referred_columns'])) for f in actual}
                    expected = {((f.parent.name,), f.column.table.name, (f.column.name,)) for f in table.foreign_keys}
                    if actual_refs != expected:
                        raise RuntimeError('Unexpected existing attribution foreign keys: ' + name)
                print(json.dumps({'table': name, 'apply': apply, 'status': 'current' if exists else 'create_planned'}))
                if not exists:
                    print(str(CreateTable(table).compile(dialect=connection.dialect)))
                    if apply:
                        await connection.run_sync(lambda c: table.create(c, checkfirst=False))
    finally:
        await engine.dispose()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-database', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    asyncio.run(migrate(get_settings().database_url, args.expected_database, apply=args.apply))
