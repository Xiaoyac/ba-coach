"""Backfill/edit correctness, immutable history, concurrency and ownership."""
import asyncio
from datetime import date, timedelta
import pytest
from app.models import AssessmentEntry
from test_daily_record_0917 import submission
from test_admin_accounts import admin_headers  # noqa: F401


def create(client, headers, on='2020-01-02'):
    body = {**submission(), 'local_date': on}
    response = client.post('/api/assessment', headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def edit_body(record):
    return {'local_date': record['local_date'], 'expected_revision': record['revision_no'],
            'activities': record['activities'], 'summary': {key: record[key] for key in (
                'completion_rate', 'completion_not_applicable', 'activity_level', 'overall_mood',
                'social_connection', 'approach_vs_avoidance', 'reflection_note')}}


def test_backfill_edit_date_and_keep_every_snapshot(client, register, admin_headers):
    headers = register('editdaily')
    first = create(client, headers)
    body = edit_body(first)
    body['local_date'] = '2020-01-01'
    body['activities'][0]['activity'] = '更正为慢走'
    body['summary']['overall_mood'] = 5
    # Keep the original expected snapshot untouched by Python references.
    original = client.get('/api/assessment/history', headers=headers).json()['items'][0]
    response = client.put(f"/api/assessment/{first['id']}", headers=headers, json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['id'] == first['id'] and saved['revision_no'] == 2
    assert saved['local_date'] == '2020-01-01'
    assert saved['activities'][0]['activity'] == '更正为慢走'
    assert saved['overall_mood'] == 5
    audit = client.get(f"/api/assessment/{first['id']}/revisions", headers=headers).json()
    assert [x['revision_no'] for x in audit] == [2, 1]
    assert audit[1]['record'] == original and audit[0]['record'] == saved
    assert client.get(f"/api/admin/assessments/{first['id']}/revisions", headers=admin_headers).json() == audit
    assert client.get('/api/assessment/by-date?local_date=2020-01-02', headers=headers).json() is None
    assert client.get('/api/assessment/by-date?local_date=2020-01-01', headers=headers).json() == saved
    third = edit_body(saved); third['summary']['reflection_note'] = '再次修改'
    assert client.put(f"/api/assessment/{first['id']}", headers=headers, json=third).status_code == 200
    revisions = client.get(f"/api/assessment/{first['id']}/revisions", headers=headers).json()
    assert [x['revision_no'] for x in revisions] == [3, 2, 1]
    assert revisions[2] == audit[1]


def test_conflict_stale_and_cross_account_never_overwrite(client, register):
    headers, other = register('dailyfirst'), register('dailysecond')
    first = create(client, headers)
    second = create(client, headers, '2020-01-03')
    body = edit_body(first); body['local_date'] = second['local_date']
    path = f"/api/assessment/{first['id']}"
    assert client.put(path, headers=headers, json=body).status_code == 409
    assert client.put(path, headers=other, json=body).status_code == 404
    assert client.get(path + '/revisions', headers=other).status_code == 404
    assert client.get('/api/assessment/by-date?local_date=2020-01-02', headers=other).json() is None
    assert client.get(f"/api/admin/assessments/{first['id']}/revisions", headers=other).status_code == 403
    assert client.put(path, json=body).status_code == 401
    body['local_date'] = first['local_date']
    assert client.put(path, headers=headers, json=body).status_code == 200
    body['summary']['overall_mood'] = 4
    assert client.put(path, headers=headers, json=body).status_code == 409
    audit = client.get(path + '/revisions', headers=headers).json()
    assert len(audit) == 2 and audit[0]['record']['overall_mood'] == first['overall_mood']


def test_future_dates_rejected_and_summary_only_edit(client, register):
    headers = register('dailyfuture')
    tomorrow = (date.today() + timedelta(days=3)).isoformat()
    assert client.post('/api/assessment', headers=headers, json={**submission(), 'local_date': tomorrow}).status_code == 422
    first = create(client, headers)
    body = edit_body(first); body['local_date'] = tomorrow
    assert client.put(f"/api/assessment/{first['id']}", headers=headers, json=body).status_code == 422
    body['local_date'] = first['local_date']; body['activities'] = []
    body['summary'].update(completion_rate=None, completion_not_applicable=True)
    response = client.put(f"/api/assessment/{first['id']}", headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()['activities'] == [] and response.json()['completion_not_applicable']
    body = edit_body(response.json()); body['summary']['overall_mood'] = 9
    assert client.put(f"/api/assessment/{first['id']}", headers=headers, json=body).status_code == 422


def test_legacy_first_edit_preserves_scale_and_baseline(client, register, db_sessionmaker):
    headers = register('olddaily')
    owner = client.get('/api/auth/me', headers=headers).json()['profile_uuid']
    async def seed():
        async with db_sessionmaker() as db:
            row = AssessmentEntry(subject_id=owner, recorded_on=date(2019, 1, 1), timezone='UTC',
                scale_version=1, completion_rate=9, activity_level=8, overall_mood=None,
                social_connection=7, approach_vs_avoidance=6, activities=[])
            db.add(row); await db.commit()
    asyncio.run(seed())
    first = client.get('/api/assessment/history', headers=headers).json()['items'][0]
    body = edit_body(first); body['local_date'] = '2019-01-02'
    response = client.put(f"/api/assessment/{first['id']}", headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()['scale_version'] == 1
    for field in ['completion_rate','activity_level','overall_mood','social_connection','approach_vs_avoidance']:
        assert response.json()[field] == first[field]
    assert client.get(f"/api/assessment/{first['id']}/revisions", headers=headers).json()[1]['record'] == first
