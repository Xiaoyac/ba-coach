"""Separate M4 factual confirmation from the later M2 choice. Dry-run by default.

Only replaces the confirmed-review CHECK; no rows or historical decisions change.
Run with the production environment loaded and --expected-database NAME.
"""
import argparse
import asyncio
import json
import re
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings

NEW_CHECK = "record_status != 'confirmed' OR (execution_result IS NOT NULL AND confirmation_message_id IS NOT NULL)"

def migration_sql(checks):
    old = [c for c in checks if all(token in c['sqltext'].lower() for token in
        ('record_status', 'execution_result', 'review_decision', 'confirmation_message_id'))]
    if not old:
        if any(all(token in c['sqltext'].lower() for token in
            ('record_status', 'execution_result', 'confirmation_message_id'))
            and 'review_decision' not in c['sqltext'].lower() for c in checks):
            return None
        raise ValueError('Expected confirmed-review constraint not found; refusing to guess')
    if len(old) != 1 or not re.fullmatch(r'[A-Za-z0-9_]+', old[0].get('name') or ''):
        raise ValueError('Ambiguous or unnamed constraint; inspect schema first')
    # MySQL ALTER validates existing rows and replaces constraints in one DDL.
    return (f"ALTER TABLE module_four_record DROP CHECK `{old[0]['name']}`, "
            f"ADD CONSTRAINT ck_m4_confirmed_attempt CHECK ({NEW_CHECK})")

async def migrate(expected_database, apply=False):
    engine = create_async_engine(get_settings().database_url)
    try:
        if engine.url.get_backend_name() != 'mysql' or engine.url.database != expected_database:
            raise ValueError('Expected MySQL database does not match configured target')
        async with engine.begin() as conn:
            checks = await conn.run_sync(lambda c: inspect(c).get_check_constraints('module_four_record'))
            sql = migration_sql(checks)
            print(json.dumps({'database': expected_database, 'apply': apply, 'sql': sql,
                'status': 'already_current' if sql is None else 'planned'}, ensure_ascii=False))
            if apply and sql:
                await conn.execute(text(sql))
                after = await conn.run_sync(lambda c: inspect(c).get_check_constraints('module_four_record'))
                if migration_sql(after) is not None:
                    raise RuntimeError('Constraint verification failed')
                print('Constraint updated; historical rows unchanged.')
    finally:
        await engine.dispose()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-database', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    asyncio.run(migrate(args.expected_database, args.apply))
