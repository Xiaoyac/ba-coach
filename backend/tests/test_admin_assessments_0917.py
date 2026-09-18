import asyncio
import csv
import io
from datetime import date

import pytest

from app.models import AssessmentEntry, ActivityLog
from app.routes.admin_assessments import safe_cell
from test_admin_accounts import admin_headers  # noqa: F401


def seed(client, register, db_sessionmaker):
    first = register('dailyowner', nickname='小河')
    second = register('dailyother', nickname='小山')
    owner = client.get('/api/auth/me', headers=first).json()['profile_uuid']
    other = client.get('/api/auth/me', headers=second).json()['profile_uuid']
    async def insert():
        async with db_sessionmaker() as db:
            old = AssessmentEntry(subject_id=owner, recorded_on=date(2026,9,15), timezone='Asia/Shanghai', status='completed',
                scale_version=1, completion_rate=7, activity_level=6, overall_mood=8, social_connection=5)
            modern = AssessmentEntry(subject_id=owner, recorded_on=date(2026,9,17), timezone='Asia/Shanghai', status='completed',
                scale_version=2, completion_rate=None, activity_level=5, overall_mood=0, reflection_note='=1+1')
            modern.activities.append(ActivityLog(position=0, time_slot='19:00–20:00', activity='散步,和朋友', emotion=0))
            modern.activities.append(ActivityLog(position=1, time_slot='20:00–21:00', activity='@SUM(A1)', emotion=4, achievement=0))
            additional = AssessmentEntry(subject_id=other, recorded_on=date(2026,9,16), timezone='Asia/Shanghai', status='completed',
                scale_version=2, completion_rate=0, activity_level=0, overall_mood=2)
            skipped = AssessmentEntry(subject_id=owner, recorded_on=date(2026,9,14), status='skipped')
            db.add_all([old,modern,additional,skipped]);await db.commit()
    asyncio.run(insert())
    return first, owner


@pytest.mark.parametrize('url', ['/api/admin/assessments', '/api/admin/assessments/export.csv', '/api/admin/assessments/export.csv?kind=activities'])
def test_access_is_admin_only(client, auth_headers, url):
    assert client.get(url).status_code == 401
    assert client.get(url,headers=auth_headers).status_code == 403


def test_filter_counts_identity_scales_and_pagination(client, register, db_sessionmaker, admin_headers):
    _, owner=seed(client,register,db_sessionmaker)
    response=client.get('/api/admin/assessments?limit=1',headers=admin_headers)
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    data=response.json()
    assert (data['total'],data['users'],data['versions'])==(3,2,{'1':1,'2':2})
    first=data['items'][0]
    assert first['username']=='dailyowner'
    assert first['subject_id']==owner
    assert first['record']['completion_not_applicable'] is True
    assert first['record']['activities'][0]['achievement'] is None
    second=client.get('/api/admin/assessments?limit=1&offset=1',headers=admin_headers).json()
    assert second['items'][0]['record']['id']!=first['record']['id']
    assert data['has_more'] is True
    filtered=client.get('/api/admin/assessments',params={'query':'dailyowner','start':'2026-09-15','end':'2026-09-15'},headers=admin_headers).json()
    assert filtered['total']==1
    assert filtered['items'][0]['record']['completion_rate']==7
    assert filtered['items'][0]['record']['scale_version']==1
    assert client.get('/api/admin/assessments?scale_version=2',headers=admin_headers).json()['total']==2
    assert client.get('/api/admin/assessments?query=%25',headers=admin_headers).json()['total']==0


@pytest.mark.parametrize('query',['start=2026-09-17&end=2026-09-16','scale_version=3','limit=1000','offset=-1'])
def test_invalid_filter_rejected(client, admin_headers, query):
    assert client.get('/api/admin/assessments?'+query,headers=admin_headers).status_code==422


def test_exports_preserve_null_scales_unicode_and_escape_formulas(client, register, db_sessionmaker, admin_headers):
    seed(client,register,db_sessionmaker)
    response=client.get('/api/admin/assessments/export.csv?query=dailyowner',headers=admin_headers)
    assert response.status_code==200,response.text
    assert response.content.startswith(b'\xef\xbb\xbf')
    assert response.headers['cache-control']=='no-store'
    rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert len(rows)==2
    modern,old=rows
    assert modern['completion_rate']=='' and modern['completion_not_applicable']=='True'
    assert modern['overall_mood']=='0' and modern['summary_scale_max']=='5'
    assert modern['reflection_note']=="'=1+1"
    assert old['completion_rate']=='7' and old['summary_scale_max']=='10'
    response=client.get('/api/admin/assessments/export.csv?query=dailyowner&kind=activities',headers=admin_headers)
    rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert len(rows)==2
    assert rows[0]['activity']=='散步,和朋友'
    assert rows[0]['achievement']=='' and rows[0]['emotion']=='0'
    assert rows[1]['activity']=="'@SUM(A1)" and rows[1]['achievement']=='0'


def test_export_cap_and_user_history_scope(client, register, db_sessionmaker, admin_headers, monkeypatch):
    headers,_=seed(client,register,db_sessionmaker)
    assert len(client.get('/api/assessment/history',headers=headers).json()['items'])==2
    monkeypatch.setattr('app.routes.admin_assessments.EXPORT_LIMIT',1)
    assert client.get('/api/admin/assessments/export.csv',headers=admin_headers).status_code==422
    assert client.get('/api/admin/assessments/export.csv?start=2026-09-17&end=2026-09-17',headers=admin_headers).status_code==200


@pytest.mark.parametrize('value',['=HYPERLINK("x")',' +1','\t=1','-2','@SUM(A1)'])
def test_csv_formula_safety(value):
    assert safe_cell(value)=="'"+value
    assert safe_cell(0)==0
    assert safe_cell(None)==''
