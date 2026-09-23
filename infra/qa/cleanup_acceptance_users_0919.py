"""Exact test-user cleanup: dry run, private row backup, FK-on transaction."""
import argparse
import asyncio
import base64
from datetime import date, datetime, timezone
from decimal import Decimal
import gzip
import json
import os
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings

EXPECTED = {
    36: ('webqa0919882980', '网页验收合成用户'),
    37: ('e2e09199ff551d6', '线上验收'), 38: ('e2e0919f10ae514', '线上验收'),
    39: ('e2e0919057bc281', '线上验收'), 40: ('e2e0919ab74de5d', '线上验收'),
    41: ('qads5b54c5b8', '小林'), 42: ('qads4b84ebda', '小林'),
    43: ('qads6ac9f9db', '小林'), 44: ('qadsd21d8836', '小林'),
    45: ('qadsa96531a0', '小林'), 46: ('qadsafe81eb6', '小林'),
    47: ('qads0faac7aa', '小林'), 48: ('qads62a62053', '小林'),
    49: ('qads9caf8fc6', '小林'), 50: ('qads3dcbbf14', '小林'),
}


def encode(value):
    if isinstance(value, (datetime, date)):
        return {'__type__': type(value).__name__, 'value': value.isoformat()}
    if isinstance(value, Decimal):
        return {'__type__': 'decimal', 'value': str(value)}
    if isinstance(value, bytes):
        return {'__type__': 'bytes', 'value': base64.b64encode(value).decode()}
    raise TypeError(type(value).__name__)


async def main(apply):
    engine = create_async_engine(get_settings().database_url)
    assert engine.dialect.name == 'mysql' and engine.url.database == 'ba_coach_260908'
    try:
        async with engine.begin() as db:
            params = {'accounts': tuple(EXPECTED)}
            accounts = (await db.execute(text('''SELECT a.id,a.username,a.profile_uuid,p.nickname,s.role
                FROM user_accounts a JOIN user_profile p ON p.uuid=a.profile_uuid
                LEFT JOIN account_settings s ON s.account_id=a.id
                WHERE a.id IN :accounts ORDER BY a.id FOR UPDATE'''), params)).mappings().all()
            assert len(accounts) == len(EXPECTED), 'Unexpected account set; no deletion'
            assert all((r['username'], r['nickname']) == EXPECTED[r['id']] and r['role'] != 'admin' for r in accounts)
            params['users'] = tuple(r['profile_uuid'] for r in accounts)
            async def ids(table, where):
                return tuple((await db.execute(text(f'SELECT id FROM `{table}` WHERE {where}'), params)).scalars()) or (None,)
            params['conversations'] = await ids('conversations', 'subject_id IN :users')
            params['goals'] = await ids('pa_goals', 'user_id IN :users')
            params['plans'] = await ids('module_two_record', 'goal_id IN :goals')
            params['cycles'] = await ids('pa_cycles', 'goal_id IN :goals')
            params['reviews'] = await ids('module_four_record', 'cycle_id IN :cycles')
            params['entries'] = await ids('assessment_entries', 'subject_id IN :users')
            legacy = 'legacy_20260910t155426z_'
            params['legacy_cycles'] = await ids(legacy + 'pa_cycles', 'subject_id IN :users')
            scopes = {}
            for name in ('ai_execution_events', 'conversations', 'assessment_entries'):
                scopes[name] = 'subject_id IN :users'
            for name in ('ba_memory', 'interaction_status', 'module_one_record', 'pa_activity_events', 'pa_goals',
                         'pa_push_checks', 'pa_push_deliveries', 'pa_push_devices', 'risk_monitoring',
                         'user_activity_constraints', 'user_module_one_state', 'user_preferences', 'user_supporters'):
                scopes[name] = 'user_id IN :users'
            for name in ('account_email_tokens', 'account_emails', 'account_handles', 'account_settings', 'auth_sessions', 'issue_reports'):
                scopes[name] = 'account_id IN :accounts'
            for name in ('conversation_messages', 'conversation_runtime_states', 'conversation_module_progress', 'ai_decision_logs', legacy + 'conversation_runtime_states'):
                scopes[name] = 'conversation_id IN :conversations'
            scopes.update({
                'ai_decision_logs': '(conversation_id IN :conversations OR (conversation_id IS NULL AND (goal_id IN :goals OR cycle_id IN :cycles)))',
                'module_two_record': 'goal_id IN :goals', 'module_three_record': 'goal_id IN :goals',
                'module_four_record': 'cycle_id IN :cycles', 'pa_cycles': 'goal_id IN :goals',
                'pa_cycle_progress': 'cycle_id IN :cycles', 'pa_goal_details': 'goal_id IN :goals',
                'pa_plan_details': 'plan_id IN :plans', 'pa_review_details': 'review_id IN :reviews',
                'activity_logs': 'entry_id IN :entries', 'profile_extensions': 'profile_uuid IN :users',
                'user_accounts': 'id IN :accounts', 'user_profile': 'uuid IN :users',
                legacy + 'user_profile': 'uuid IN :users', legacy + 'pa_cycles': 'subject_id IN :users',
                'clinical_record_cycle_links': 'cycle_id IN :legacy_cycles',
            })
            for suffix in ('module_one_record', 'module_two_record', 'module_three_record', 'module_four_record'):
                scopes[legacy + suffix] = 'user_id IN :users'
            tables = set((await db.execute(text('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()'))).scalars())
            scopes = {k: v for k, v in scopes.items() if k in tables}
            fks = (await db.execute(text('''SELECT TABLE_NAME,COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE()
                AND REFERENCED_TABLE_NAME IS NOT NULL'''))).mappings().all()
            for fk in fks:
                parent, child = fk['REFERENCED_TABLE_NAME'], fk['TABLE_NAME']
                if parent not in scopes:
                    continue
                cross = await db.scalar(text(f'''SELECT COUNT(*) FROM `{child}` WHERE `{fk['COLUMN_NAME']}` IN
                    (SELECT `{fk['REFERENCED_COLUMN_NAME']}` FROM `{parent}` WHERE {scopes[parent]})
                    AND NOT COALESCE(({scopes.get(child, 'FALSE')}), FALSE)'''), params)
                assert not cross, 'Cross-scope reference: ' + json.dumps(dict(fk)) + ' rows=' + str(cross)
            rows = {table: [dict(r) for r in (await db.execute(text(f'SELECT * FROM `{table}` WHERE {where} FOR UPDATE'), params)).mappings()]
                    for table, where in scopes.items()}
            counts = {table: len(data) for table, data in rows.items() if data}
            untouched_counts = {table: await db.scalar(text(f'SELECT COUNT(*) FROM `{table}` WHERE NOT COALESCE(({where}),FALSE)'), params)
                                for table, where in scopes.items()}
            summary = {'accounts': [dict(r) for r in accounts], 'counts': counts,
                       'applied': False, 'foreign_key_checks': True}
            if not apply:
                print(json.dumps(summary, ensure_ascii=False)); return
            root = Path('/opt/bacoach/backups/test-user-cleanup')
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup = root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json.gz')
            with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as raw_file:
                with gzip.GzipFile(fileobj=raw_file, mode='wb') as compressed:
                    compressed.write(json.dumps({'format': 1, 'tables': rows}, ensure_ascii=False, default=encode).encode())
            # Cycles are deleted before their plans/contracts below. Clearing
            # those required links first violates completed-cycle CHECKs.
            for table, columns in {'pa_goals': ('replaced_by_goal_id',), 'ba_memory': ('supersedes_id',),
                                   'user_activity_constraints': ('supersedes_id',)}.items():
                await db.execute(text(f'UPDATE `{table}` SET ' + ','.join(f'`{col}`=NULL' for col in columns)
                                      + f' WHERE {scopes[table]}'), params)
            order = ['ai_execution_events', 'ai_decision_logs', 'conversation_runtime_states',
                'pa_push_checks', 'pa_push_deliveries', 'pa_push_devices', 'pa_activity_events',
                'pa_review_details', 'module_four_record', 'pa_cycle_progress', 'pa_cycles',
                'module_three_record', 'pa_plan_details', 'module_two_record', 'pa_goal_details',
                'pa_goals', 'user_module_one_state', 'module_one_record', 'ba_memory',
                'user_activity_constraints', 'user_preferences', 'user_supporters',
                'clinical_record_cycle_links', legacy + 'pa_cycles', legacy + 'conversation_runtime_states',
                'conversation_module_progress', 'conversation_messages', 'conversations',
                'activity_logs', 'assessment_entries', 'risk_monitoring', 'interaction_status', 'profile_extensions',
                'issue_reports', 'account_email_tokens', 'account_emails', 'account_handles', 'auth_sessions',
                'account_settings', 'user_accounts', *[legacy + n for n in ('module_four_record', 'module_three_record',
                'module_two_record', 'module_one_record', 'user_profile')], 'user_profile']
            assert set(order) >= set(scopes)
            for table in order:
                if table in scopes:
                    await db.execute(text(f'DELETE FROM `{table}` WHERE {scopes[table]}'), params)
            remaining = {table: await db.scalar(text(f'SELECT COUNT(*) FROM `{table}` WHERE {where}'), params)
                         for table, where in scopes.items()}
            assert not any(remaining.values()), 'Residual scoped rows; transaction rolls back'
            for table, where in scopes.items():
                assert await db.scalar(text(f'SELECT COUNT(*) FROM `{table}` WHERE NOT COALESCE(({where}),FALSE)'), params) == untouched_counts[table], 'Non-target row count changed: ' + table
            summary.update(applied=True, backup=str(backup), residual_rows=0)
        print(json.dumps(summary, ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args().apply))
