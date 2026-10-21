"""Administrator daily records: ordinary people -> all dates -> full day."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models import ActivityLog, AssessmentEntry, AssessmentRevision, UserAccount
from app.routes import admin_assessments
from test_admin_accounts import admin_headers  # noqa: F401


TODAY = date(2026, 10, 9)
server_today = admin_assessments._today


@pytest.fixture(autouse=True)
def beijing_today(monkeypatch):
    monkeypatch.setattr(admin_assessments, '_today', lambda: TODAY)


def save(db_sessionmaker, *rows):
    async def insert():
        async with db_sessionmaker() as db:
            db.add_all(rows)
            await db.commit()
    asyncio.run(insert())


def subject(client, headers):
    return client.get('/api/auth/me', headers=headers).json()['profile_uuid']


def completed(subject_id, on, count=0):
    entry = AssessmentEntry(subject_id=subject_id, recorded_on=on, timezone='Asia/Shanghai',
        status='completed', scale_version=2,
        reflection_note='今天记录的完整反思')
    # Insert reversed to cover activity ordering as well as complete hydration.
    entry.activities = [ActivityLog(position=i, time_slot=f'{i:02}:00', activity=f'活动 {i}',
        emotion=0, achievement=None, note=f'备注 {i}') for i in reversed(range(count))]
    return entry


@pytest.mark.parametrize('path', ['/users', '/users/example/dates', '/users/example/days/2026-10-09'])
def test_directory_requires_admin(client, auth_headers, path):
    assert client.get('/api/admin/assessments' + path).status_code == 401
    assert client.get('/api/admin/assessments' + path, headers=auth_headers).status_code == 403


def test_directory_includes_zero_records_excludes_admin_and_counts_only_recorded_today(
    client, register, admin_headers, db_sessionmaker
):
    completed_id = subject(client, register('filled', nickname='小河'))
    backfill_id = subject(client, register('backfilled'))
    empty_id = subject(client, register('neverfilled'))
    admin_id = subject(client, admin_headers)
    record = completed(completed_id, TODAY, count=3)
    # Completion is about the record's date, not its creation/update timestamp.
    record.created_at = datetime(2026, 10, 8, 15, 59, tzinfo=timezone.utc)
    save(db_sessionmaker, record, completed(completed_id, TODAY - timedelta(days=1)),
        completed(backfill_id, TODAY - timedelta(days=1)),
        AssessmentEntry(subject_id=backfill_id, recorded_on=TODAY, status='skipped'),
        completed(admin_id, TODAY))

    response = client.get('/api/admin/assessments/users', headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    data = response.json()
    assert data['today'] == '2026-10-09' and data['time_zone'] == 'Asia/Shanghai'
    assert data['total'] == 3 and data['has_more'] is False
    people = {row['subject_id']: row for row in data['items']}
    assert set(people) == {completed_id, backfill_id, empty_id}
    assert people[completed_id]['today_completed'] is True
    assert people[completed_id]['record_count'] == people[completed_id]['date_count'] == 2
    assert people[completed_id]['last_record_date'] == '2026-10-09'
    assert people[completed_id]['nickname'] == '小河'
    assert people[backfill_id]['today_completed'] is False
    assert people[backfill_id]['record_count'] == 1
    assert people[empty_id]['today_completed'] is False
    assert people[empty_id]['record_count'] == people[empty_id]['date_count'] == 0
    assert people[empty_id]['last_record_date'] is None


def test_search_is_literal_and_covers_username_nickname_and_profile_uuid(
    client, register, admin_headers, db_sessionmaker
):
    target_id = subject(client, register('dailysearch', nickname='搜索小河'))
    register('anotheraccount', nickname='搜索小山')
    for needle in ('dailysearch', '搜索小河', target_id):
        result = client.get('/api/admin/assessments/users', params={'query': needle}, headers=admin_headers)
        assert result.status_code == 200, result.text
        assert [row['subject_id'] for row in result.json()['items']] == [target_id]
    for needle in ('%', '_', '\\'):
        result = client.get('/api/admin/assessments/users', params={'query': needle}, headers=admin_headers)
        assert result.json()['items'] == []


def test_zero_record_users_with_legacy_missing_settings_paginate_without_loss(
    client, admin_headers, db_sessionmaker
):
    rows = [UserAccount(username=f'legacyuser{i:02}', password_hash='not-a-login-password',
        profile_uuid=str(uuid4())) for i in range(23)]
    save(db_sessionmaker, *rows)
    first = client.get('/api/admin/assessments/users', headers=admin_headers).json()
    second = client.get('/api/admin/assessments/users?offset=20', headers=admin_headers).json()
    assert first['total'] == second['total'] == 23
    assert len(first['items']) == 20 and first['has_more'] is True
    assert len(second['items']) == 3 and second['has_more'] is False
    listed = first['items'] + second['items']
    assert len({row['subject_id'] for row in listed}) == 23
    assert all(row['record_count'] == 0 and row['today_completed'] is False for row in listed)


def test_all_dates_and_full_day_activities_are_returned_without_twenty_item_limit(
    client, register, admin_headers, db_sessionmaker
):
    owner_id = subject(client, register('manydates'))
    other_id = subject(client, register('otherdates'))
    entries = [completed(owner_id, TODAY - timedelta(days=i), count=25 if i == 0 else 0)
               for i in range(25)]
    save(db_sessionmaker, *entries, completed(other_id, TODAY, count=2),
         AssessmentEntry(subject_id=owner_id, recorded_on=TODAY - timedelta(days=30), status='skipped'))
    response = client.get(f'/api/admin/assessments/users/{owner_id}/dates', headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    data = response.json()
    assert data['subject_id'] == owner_id and data['total_dates'] == 25
    assert [item['local_date'] for item in data['items']] == [
        (TODAY - timedelta(days=i)).isoformat() for i in range(25)]
    assert data['items'][0] == {'local_date': TODAY.isoformat(), 'record_count': 1, 'activity_count': 25}
    assert data['items'][1]['activity_count'] == 0

    response = client.get(f'/api/admin/assessments/users/{owner_id}/days/{TODAY}', headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    day = response.json()
    assert day['total'] == 1 and day['local_date'] == TODAY.isoformat()
    assert {row['subject_id'] for row in day['items']} == {owner_id}
    record = day['items'][0]['record']
    assert record['scale_version'] == 2 and 'overall_mood' not in record
    assert record['reflection_note'] == '今天记录的完整反思'
    assert len(record['activities']) == 25
    assert [row['position'] for row in record['activities']] == list(range(25))
    assert record['activities'][0]['emotion'] == 0 and record['activities'][0]['achievement'] is None
    assert record['activities'][-1]['note'] == '备注 24'
    skipped = client.get(f'/api/admin/assessments/users/{owner_id}/days/{TODAY - timedelta(days=30)}',
                         headers=admin_headers).json()
    assert skipped['items'] == [] and skipped['total'] == 0


def test_date_changes_use_current_record_date_and_preserve_revision_history(
    client, register, admin_headers, db_sessionmaker
):
    owner_id = subject(client, register('changeddate'))
    entry = completed(owner_id, TODAY - timedelta(days=1), count=1)
    entry.revision_no = 2
    save(db_sessionmaker, entry)
    snapshot = admin_assessments._to_out(entry).model_dump(mode='json')
    snapshot.update(local_date=TODAY.isoformat(), revision_no=1)
    save(db_sessionmaker, AssessmentRevision(entry_id=entry.id, revision_no=1, snapshot=snapshot))
    user = client.get('/api/admin/assessments/users?query=changeddate', headers=admin_headers).json()['items'][0]
    assert user['today_completed'] is False
    dates = client.get(f'/api/admin/assessments/users/{owner_id}/dates', headers=admin_headers).json()
    assert [row['local_date'] for row in dates['items']] == ['2026-10-08']
    history = client.get(f'/api/admin/assessments/{entry.id}/revisions', headers=admin_headers)
    assert history.status_code == 200, history.text
    assert history.json()[0]['record']['local_date'] == '2026-10-09'


def test_empty_user_and_missing_or_admin_subjects(client, register, admin_headers):
    empty_id = subject(client, register('emptydates'))
    dates = client.get(f'/api/admin/assessments/users/{empty_id}/dates', headers=admin_headers).json()
    assert dates['items'] == [] and dates['total_dates'] == 0
    for invalid_id in ('does-not-exist', subject(client, admin_headers)):
        for suffix in ('dates', 'days/2026-10-09'):
            response = client.get(f'/api/admin/assessments/users/{invalid_id}/{suffix}', headers=admin_headers)
            assert response.status_code == 404
            assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('query', ['limit=0', 'limit=51', 'offset=-1', 'query=' + 'x' * 65])
def test_directory_pagination_and_search_validation(client, admin_headers, query):
    assert client.get('/api/admin/assessments/users?' + query, headers=admin_headers).status_code == 422


def test_server_today_uses_beijing_midnight(monkeypatch):
    # Exercise the actual helper across UTC 16:00 rather than patching its return.
    class Clock(datetime):
        value = datetime(2026, 10, 8, 15, 59, 59, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.value.astimezone(tz)

    monkeypatch.setattr(admin_assessments, 'datetime', Clock)
    assert server_today() == date(2026, 10, 8)
    Clock.value = datetime(2026, 10, 8, 16, 0, 0, tzinfo=timezone.utc)
    assert server_today() == date(2026, 10, 9)
