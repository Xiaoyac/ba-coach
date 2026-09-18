"""Read-only production checks. Never prints assessment contents or auth secrets."""
import asyncio
import json
import os
from pathlib import Path

from dotenv import dotenv_values
for key, value in dotenv_values('/etc/bacoach/backend.env').items():
    if value is not None:
        os.environ[key] = value

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload
from app.db import get_sessionmaker
from app.models import AssessmentEntry
from app.routes.assessment import _to_out
from app.prompts import build_system_segments


async def main():
    backup_path = Path('/opt/bacoach/backups/daily-record-before-20260917T051016Z.json')
    backup = json.loads(backup_path.read_text())
    async with get_sessionmaker()() as db:
        if await db.scalar(text('SELECT DATABASE()')) != 'ba_coach_260908':
            raise RuntimeError('Unexpected database')
        entries = list((await db.scalars(select(AssessmentEntry).options(selectinload(AssessmentEntry.activities)))).all())
        for entry in entries:
            _to_out(entry)
        print('EXISTING_RECORDS_SERIALIZE', len(entries))
        for table, snapshot in backup['tables'].items():
            if table not in {'assessment_entries', 'activity_logs'}:
                raise RuntimeError('Unexpected backup table')
            current = {row['id']: dict(row) for row in (await db.execute(text(f'SELECT * FROM `{table}`'))).mappings()}
            for original in snapshot['rows']:
                saved = current[original['id']]
                actual = json.loads(json.dumps({key: saved[key] for key in original}, default=str))
                if actual != original:
                    raise RuntimeError(f'Historical row changed in {table}; inspect privately')
            print('HISTORICAL_ROWS_UNCHANGED', table, len(snapshot['rows']))
    prompt = '\n'.join(s.text for s in build_system_segments('module_3', module_prompt='custom'))
    assert '打开每日记录' in prompt and '不等于0分' in prompt
    print('M3_GUIDANCE_PRESENT')


if __name__ == '__main__':
    asyncio.run(main())
