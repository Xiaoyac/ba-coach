"""Upgrade only chat text columns; preserve identifier/FK collations and data.

MySQL DDL auto-commits. This migration is idempotent and backs up affected
columns plus table DDL before applying. --apply is required to mutate schema.
"""
import argparse
import asyncio
import gzip
import json
import os
from pathlib import Path
from sqlalchemy import text
from app.db import get_engine

TARGETS = {
    'conversation_messages': {'content': ('text', False), 'reasoning_content': ('longtext', True),
                              'routing_reasoning_content': ('longtext', True)},
    'conversations': {'title': ('varchar(80)', False)},
}


def modification(column, row, expected):
    kind, nullable = expected
    if (row['COLUMN_TYPE'].lower() != kind or row['IS_NULLABLE'] != ('YES' if nullable else 'NO')
            or row['COLUMN_DEFAULT'] is not None or row['EXTRA']):
        raise RuntimeError(f'Unexpected column definition: {column}')
    if row['CHARACTER_SET_NAME'] == 'utf8mb4':
        return None
    if row['CHARACTER_SET_NAME'] not in ('utf8', 'utf8mb3'):
        raise RuntimeError(f'Unsupported source charset: {column}')
    if row['COLLATION_NAME'] not in ('utf8_general_ci', 'utf8mb3_general_ci'):
        raise RuntimeError(f'Unexpected collation: {column}')
    return f'MODIFY COLUMN `{column}` {kind} CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci ' + ('NULL' if nullable else 'NOT NULL')


async def migrate(*, expected_database=None, apply=False, backup_path=None):
    engine = get_engine()
    try:
        assert engine.dialect.name == 'mysql', 'MySQL only'
        database = engine.url.database
        if apply and (database != expected_database or not backup_path):
            raise RuntimeError('Apply requires matching --expected-database and --backup')
        async with engine.connect() as conn:
            await conn.execute(text('SET SESSION lock_wait_timeout = 10'))
            plan = []; backup = {'database': database, 'tables': {}}
            for table, columns in TARGETS.items():
                rows = (await conn.execute(text('SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA,CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t'), {'t': table})).mappings()
                by_name = {r['COLUMN_NAME']: dict(r) for r in rows}
                changes = [modification(c, by_name[c], expected) for c, expected in columns.items()]
                changes = [c for c in changes if c]
                if changes:
                    plan.append('ALTER TABLE `' + table + '` ' + ', '.join(changes))
                    if apply:
                        ddl = (await conn.execute(text(f'SHOW CREATE TABLE `{table}`'))).one()[1]
                        fields = ','.join('`'+c+'`' for c in ['id', *columns])
                        data = [dict(r) for r in (await conn.execute(text(f'SELECT {fields} FROM `{table}` ORDER BY id'))).mappings()]
                        backup['tables'][table] = {'ddl': ddl, 'rows': data}
            if apply and plan:
                path = Path(backup_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'wb') as raw:
                    with gzip.GzipFile(fileobj=raw, mode='wb') as zipped:
                        zipped.write(json.dumps(backup, ensure_ascii=False).encode())
                    raw.flush(); os.fsync(raw.fileno())
                await conn.commit()
                for sql in plan:
                    await conn.execute(text(sql))
                    await conn.commit()
                for table, item in backup['tables'].items():
                    fields = ','.join('`'+c+'`' for c in ['id', *TARGETS[table]])
                    after = {r['id']: dict(r) for r in (await conn.execute(text(f'SELECT {fields} FROM `{table}`'))).mappings()}
                    # Message text is immutable. Conversation titles may be
                    # renamed concurrently, so only transcript content is compared.
                    if table == 'conversation_messages':
                        assert all(after.get(r['id']) == r for r in item['rows']), 'Transcript changed during migration'
                result = (await conn.execute(text("SELECT COLUMN_NAME,CHARACTER_SET_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='conversation_messages' AND COLUMN_NAME IN ('content','reasoning_content','routing_reasoning_content')"))).all()
                assert all(r[1] == 'utf8mb4' for r in result)
            return {'database': database, 'applied': apply, 'statements': plan,
                    'backed_up_rows': {t: len(v['rows']) for t,v in backup['tables'].items()}}
    finally:
        await engine.dispose()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-database')
    parser.add_argument('--backup')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(migrate(expected_database=args.expected_database, apply=args.apply, backup_path=args.backup)), indent=2))
