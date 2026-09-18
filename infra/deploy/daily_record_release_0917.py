"""Server-side scoped backup/preflight for daily-record migration; never prints rows or secrets."""
import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

for key, value in dotenv_values('/etc/bacoach/backend.env').items():
    if value is not None:
        os.environ[key] = value
from app.config import get_settings


async def main(args):
    engine = create_async_engine(get_settings().database_url)
    try:
        if engine.dialect.name != 'mysql':
            raise RuntimeError('Expected MySQL')
        print('DATABASE', engine.url.database)
        if args.backup and engine.url.database != args.expected_database:
            raise RuntimeError('Explicit target mismatch')
        async with engine.connect() as conn:
            print('VERSION', await conn.scalar(text('SELECT VERSION()')))
            snapshot = {'database': engine.url.database, 'tables': {}}
            for table in ('assessment_entries', 'activity_logs'):
                columns = await conn.run_sync(lambda c: inspect(c).get_columns(table))
                print(table, [(c['name'], str(c['type']), c['nullable']) for c in columns if c['name'] in ('scale_version', 'emotion', 'achievement', 'connection', 'enjoyment', 'importance')])
                if args.backup:
                    ddl = (await conn.execute(text(f'SHOW CREATE TABLE `{table}`'))).one()[1]
                    rows = [dict(row) for row in (await conn.execute(text(f'SELECT * FROM `{table}` ORDER BY id'))).mappings()]
                    snapshot['tables'][table] = {'ddl': ddl, 'rows': rows}
                    print('BACKUP_ROWS', table, len(rows))
            if args.backup:
                folder = Path('/opt/bacoach/backups')
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                target = folder / f'daily-record-before-{stamp}.json'
                data = json.dumps(snapshot, ensure_ascii=False, default=str).encode()
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                print('BACKUP', target, 'SHA256', hashlib.sha256(data).hexdigest())
    finally:
        await engine.dispose()
    from migrate_daily_record_0917 import migrate
    if args.expected_database:
        if args.apply_migration and not args.backup:
            raise RuntimeError('Migration requires a fresh server-side backup')
        await migrate(get_settings().database_url, expected_database=args.expected_database, apply=args.apply_migration)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup', action='store_true')
    parser.add_argument('--expected-database')
    parser.add_argument('--apply-migration', action='store_true')
    asyncio.run(main(parser.parse_args()))
