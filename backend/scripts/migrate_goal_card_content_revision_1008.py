"""Add a content counter; keep concurrency revisions intact. Dry-run by default.

Stop card writes while applying. MySQL DDL may auto-commit. Missing historical
snapshots produce NULL, never an invented content version.
"""
import argparse
import asyncio
import json
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


def normalized(fields):
    return {k: v for k, v in (fields or {}).items() if v is not None and v != ""}


def count_versions(snapshots, current_fields):
    if not snapshots or snapshots[0].get('revision') != 1:
        return None
    count, previous = 1, normalized(snapshots[0].get('fields'))
    for snapshot in snapshots[1:]:
        fields = normalized(snapshot.get('fields'))
        if fields != previous:
            count += 1
            previous = fields
    return count if previous == normalized(current_fields) else None


async def migrate(url, *, apply=False, expected_database=None):
    engine = create_async_engine(url)
    try:
        if expected_database is not None and engine.url.database != expected_database:
            raise ValueError("Database does not match --expected-database")
        async with engine.begin() as db:
            columns = await db.run_sync(lambda c: {x['name'] for x in inspect(c).get_columns('goal_card_workspaces')})
            if not apply:
                return {'status': 'already_present' if 'content_revision' in columns else 'dry_run', 'column': 'content_revision', 'nullable': True}
            added = 'content_revision' not in columns
            if added:
                await db.execute(text('ALTER TABLE goal_card_workspaces ADD COLUMN content_revision INTEGER DEFAULT NULL'))
            # Resume after a DDL auto-commit or interrupted backfill. Never reset
            # counters already maintained by the running application.
            rows = (await db.execute(text('SELECT id, fields FROM goal_card_workspaces WHERE content_revision IS NULL'))).mappings().all()
            logs = (await db.execute(text("SELECT decision_value FROM ai_decision_logs WHERE decision_type = 'goal_card_revision' ORDER BY id"))).scalars().all()
            snapshots = {}
            for raw in logs:
                item = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(item, dict) and item.get('id'):
                    snapshots.setdefault(item['id'], []).append(item)
            unknown = 0
            for row in rows:
                fields = json.loads(row['fields']) if isinstance(row['fields'], str) else row['fields']
                count = count_versions(snapshots.get(row['id'], []), fields)
                unknown += count is None
                await db.execute(text('UPDATE goal_card_workspaces SET content_revision = :count WHERE id = :id'), {'count': count, 'id': row['id']})
            return {'status': 'applied' if added or rows else 'already_present', 'rows': len(rows), 'unknown_history': unknown}
    finally:
        await engine.dispose()


if __name__ == '__main__':
    from app.config import get_settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-database', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(migrate(get_settings().database_url, apply=args.apply, expected_database=args.expected_database))))
