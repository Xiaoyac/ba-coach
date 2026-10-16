"""Retire the unused public-tag table without changing account/profile ownership.

Stop application writers first. Default is read-only. --apply requires a new
--backup file. --restore restores that file before rolling back the old code.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys

from sqlalchemy import MetaData, Table, inspect, select, text
from sqlalchemy.schema import CreateIndex, CreateTable


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, default=str,
                                    sort_keys=True).encode()).hexdigest()


def invariants(conn):
    """Only hashes/counts are emitted; credentials and clinical text are not."""
    names = set(inspect(conn).get_table_names())
    result = {}
    for name, columns, order in [
        ('user_accounts', 'id, username, password_hash, profile_uuid', 'id'),
        ('user_profile', 'uuid, nickname', 'uuid'),
    ]:
        rows = [dict(r) for r in conn.execute(text(f'SELECT {columns} FROM {name} ORDER BY {order}')).mappings()]
        result[name] = {'count': len(rows), 'sha256': digest(rows)}
    for name in ('conversations', 'conversation_messages', 'assessment_entries', 'pa_goals', 'pa_cycles'):
        if name in names:
            result[name] = conn.execute(text(f'SELECT COUNT(*) FROM {name}')).scalar_one()
    return result


def migrate(conn, *, backup: Path | None = None, apply: bool = False):
    before = invariants(conn)
    if not inspect(conn).has_table('account_handles'):
        return {'table_present': False, 'changed': False, 'invariants': before}
    table = Table('account_handles', MetaData(), autoload_with=conn)
    rows = [dict(row) for row in conn.execute(select(table).order_by(table.c.account_id)).mappings()]
    if not apply:
        return {'table_present': True, 'rows': len(rows), 'changed': False, 'invariants': before}
    if backup is None:
        raise ValueError('A backup path is required')
    package = {'version': 1, 'dialect': conn.dialect.name,
               'create_table': str(CreateTable(table).compile(dialect=conn.dialect)),
               'create_indexes': [str(CreateIndex(i).compile(dialect=conn.dialect)) for i in table.indexes],
               'rows': rows, 'rows_sha256': digest(rows), 'invariants': before}
    with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
        json.dump(package, out, ensure_ascii=False, default=str)
        out.flush()
        os.fsync(out.fileno())
    table.drop(conn)
    after = invariants(conn)
    if before != after:
        raise RuntimeError('Account/profile invariants changed')
    return {'table_present': False, 'removed_rows': len(rows), 'changed': True, 'invariants': after}


def restore(conn, backup: Path):
    package = json.loads(backup.read_text())
    assert package['version'] == 1 and package['dialect'] == conn.dialect.name
    assert digest(package['rows']) == package['rows_sha256'], 'Backup checksum mismatch'
    if inspect(conn).has_table('account_handles'):
        raise RuntimeError('Refusing to replace an existing account_handles table')
    conn.execute(text(package['create_table']))
    for ddl in package['create_indexes']:
        conn.execute(text(ddl))
    table = Table('account_handles', MetaData(), autoload_with=conn)
    rows = package['rows']
    for row in rows:
        if row.get('created_at'):
            row['created_at'] = datetime.fromisoformat(row['created_at'])
    if rows:
        conn.execute(table.insert(), rows)
    actual = [dict(row) for row in conn.execute(select(table).order_by(table.c.account_id)).mappings()]
    assert digest(actual) == package['rows_sha256'], 'Restoration checksum mismatch'
    return {'restored_rows': len(rows)}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true')
    group.add_argument('--restore', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if (args.apply or args.restore) and args.backup is None:
        parser.error('--backup is required for apply/restore')
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.db import get_engine, dispose_db
    try:
        async with get_engine().begin() as conn:
            result = await conn.run_sync(lambda c: restore(c, args.backup) if args.restore
                                         else migrate(c, backup=args.backup, apply=args.apply))
        print(json.dumps(result))
    finally:
        await dispose_db()


if __name__ == '__main__':
    asyncio.run(main())
