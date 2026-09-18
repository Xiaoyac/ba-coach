"""Read-only deployed query/CSV smoke test against MySQL. No user contents printed."""
import asyncio
import csv
import io
import os
from types import SimpleNamespace

from dotenv import dotenv_values
for key, value in dotenv_values('/etc/bacoach/backend.env').items():
    if value is not None:
        os.environ[key] = value

from fastapi import Response
from sqlalchemy import text
from app.db import get_sessionmaker
from app.routes.admin_assessments import export_records, filters, list_records


async def main():
    async with get_sessionmaker()() as db:
        assert await db.scalar(text('SELECT DATABASE()')) == 'ba_coach_260908'
        criteria = filters(query='', start=None, end=None, scale_version=None)
        response = Response()
        result = await list_records(response=response, clauses=criteria, limit=20, offset=0, db=db)
        assert response.headers['cache-control'] == 'no-store'
        print('MYSQL_LIST_OK', 'records', result['total'], 'users', result['users'], 'versions', result['versions'])
        for version in (1, 2):
            subset = await list_records(response=Response(), clauses=filters(query='', start=None, end=None, scale_version=version), limit=20, offset=0, db=db)
            assert all(item['record'].scale_version == version for item in subset['items'])
        print('VERSION_FILTERS_OK')
        if result['items']:
            criteria = filters(query=result['items'][0]['subject_id'], start=None, end=None, scale_version=None)
            subset = await list_records(response=Response(), clauses=criteria, limit=20, offset=0, db=db)
            assert subset['users'] == 1
            print('USER_FILTER_OK')
        # Direct function checks, not impersonation or a production auth override.
        # This actor is only an audit marker; no account, token or session is created.
        caller = SimpleNamespace(account=SimpleNamespace(id='deployment-readonly-check'))
        for kind in ('daily', 'activities'):
            exported = await export_records(kind=kind, clauses=criteria, caller=caller, db=db)
            assert exported.headers['cache-control'] == 'no-store'
            reader = csv.DictReader(io.StringIO(exported.body.decode('utf-8-sig')))
            assert 'scale_version' in reader.fieldnames and 'completion_not_applicable' in reader.fieldnames
            count = sum(1 for _ in reader)
            print('CSV_OK', kind, 'rows', count)
        print('NO_DATA_WRITES_OR_EXPORT_FILES')


if __name__ == '__main__':
    asyncio.run(main())
