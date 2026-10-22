"""每日测量: a standalone daily questionnaire, its edits, research IDs and the research export."""
import asyncio
import csv
import io

import pytest
from sqlalchemy import func, select

from app.models import DailyMeasurement, DailyMeasurementRevision
from test_admin_accounts import admin_headers  # noqa: F401
from test_daily_record_0917 import submission

URL = '/api/daily-measurements'


def answers():
    return {'sleep_hours': 6.5, 'sleep_quality': 2,
            'completion': 4, 'activity_level': 3, 'overall_mood': 5,
            'energetic': 1, 'peaceful': 2, 'secure': 3, 'low_mood': 4, 'worthless': 5,
            'irritable': 1, 'tired': 2, 'stressed': 3, 'lonely': 4,
            'vigorous_count': 1, 'vigorous_minutes': 0, 'moderate_count': 0, 'moderate_minutes': 0,
            'light_count': 2, 'light_minutes': 30}


def measure(client, headers, on='2020-03-01', **change):
    response = client.post(URL, headers=headers, json={
        'timezone': 'Asia/Shanghai', 'local_date': on, 'answers': {**answers(), **change}})
    assert response.status_code == 201, response.text
    return response.json()


def account_id(client, admin_headers, username):
    accounts = client.get(f'/api/admin/accounts?query={username}', headers=admin_headers).json()['accounts']
    return next(item['id'] for item in accounts if item['username'] == username)


def test_saved_returned_edited_and_snapshotted(client, register):
    headers = register('measureowner')
    saved = measure(client, headers)
    assert saved['questionnaire_version'] == 'M1' and saved['revision_no'] == 1
    assert saved['answers'] == answers() and saved['local_date'] == '2020-03-01'
    assert client.get(f'{URL}/by-date?local_date=2020-03-01', headers=headers).json()['id'] == saved['id']
    assert client.get(f'{URL}/by-date?local_date=2020-03-02', headers=headers).json() is None

    changed = {**answers(), 'lonely': 1, 'sleep_hours': 8, 'moderate_count': 1, 'moderate_minutes': 20}
    body = {'local_date': '2020-03-01', 'expected_revision': 1, 'answers': changed}
    edited = client.put(f"{URL}/{saved['id']}", headers=headers, json=body)
    assert edited.status_code == 200, edited.text
    assert edited.json()['answers'] == changed and edited.json()['revision_no'] == 2
    assert edited.json()['submitted_at'] == saved['submitted_at']
    # A stale revision is refused rather than overwriting the newer answers.
    assert client.put(f"{URL}/{saved['id']}", headers=headers, json=body).status_code == 409
    revisions = client.get(f"{URL}/{saved['id']}/revisions", headers=headers).json()
    assert [row['measurement']['answers']['sleep_hours'] for row in revisions] == [8, 6.5]

    history = client.get(f'{URL}/history', headers=headers).json()
    assert [item['id'] for item in history['items']] == [saved['id']] and history['has_more'] is False


def test_one_per_day_owner_only_and_no_future(client, register):
    headers = register('measureday')
    saved = measure(client, headers)
    duplicate = client.post(URL, headers=headers, json={'local_date': '2020-03-01', 'answers': answers()})
    assert duplicate.status_code == 409
    other = measure(client, headers, on='2020-03-02')
    move = {'local_date': '2020-03-02', 'expected_revision': 1, 'answers': answers()}
    assert client.put(f"{URL}/{saved['id']}", headers=headers, json=move).status_code == 409
    assert client.post(URL, headers=headers, json={'local_date': '2999-01-01', 'answers': answers()}).status_code == 422

    stranger = register('measurestranger')
    assert client.get(f"{URL}/{other['id']}/revisions", headers=stranger).status_code == 404
    assert client.put(f"{URL}/{other['id']}", headers=stranger, json=move).status_code == 404
    assert client.get(f'{URL}/by-date?local_date=2020-03-02', headers=stranger).json() is None


@pytest.mark.parametrize('change', [
    {'completion': None}, {'completion': 0}, {'overall_mood': 6}, {'energetic': 0}, {'lonely': 6},
    {'light_count': -1}, {'light_minutes': 1441},
    # Each intensity is capped at 20 occurrences and 480 minutes.
    {'vigorous_count': 21}, {'light_count': 21, 'light_minutes': 30}, {'moderate_minutes': 481, 'moderate_count': 1},
    {'sleep_hours': None}, {'sleep_hours': -1}, {'sleep_hours': 24.5}, {'sleep_hours': 7.25},
    {'sleep_quality': None}, {'sleep_quality': 0}, {'sleep_quality': 5},
    # Minutes without a single occurrence, and more minutes than the day has.
    {'moderate_count': 0, 'moderate_minutes': 10},
    {'light_minutes': 800, 'vigorous_minutes': 700},
])
def test_every_item_is_required_and_bounded(client, register, change):
    body = {k: v for k, v in {**answers(), **change}.items() if v is not None}
    response = client.post(URL, headers=register('measureinvalid'), json={'answers': body})
    assert response.status_code == 422, response.text


def test_activity_caps_are_inclusive(client, register):
    saved = measure(client, register('measurecap'), vigorous_count=20, vigorous_minutes=480,
                    moderate_count=20, moderate_minutes=480, light_count=20, light_minutes=480)
    assert saved['answers']['vigorous_count'] == 20 and saved['answers']['light_minutes'] == 480


def test_zero_activity_and_counts_without_minutes_are_valid(client, register):
    saved = measure(client, register('measurezero'), sleep_hours=0, sleep_quality=4, light_count=0,
                    light_minutes=0, moderate_count=3, moderate_minutes=0, vigorous_count=0, vigorous_minutes=0)
    assert saved['answers']['moderate_count'] == 3 and saved['answers']['sleep_hours'] == 0


def test_daily_record_is_independent_of_the_measurement(client, register):
    headers = register('measureisolated')
    record = client.post('/api/assessment', headers=headers, json=submission())
    assert record.status_code == 201, record.text
    assert record.json()['scale_version'] == 2 and 'r3' not in record.json()
    assert client.get(f"{URL}/by-date?local_date={record.json()['local_date']}", headers=headers).json() is None
    saved = measure(client, headers, on=record.json()['local_date'])
    assert client.get('/api/assessment/by-date?local_date=' + saved['local_date'], headers=headers).json()['id'] == record.json()['id']


def test_research_id_is_admin_only_unique_and_clearable(client, register, admin_headers):
    user_headers = register('measureparticipant')
    register('measureother')
    target = account_id(client, admin_headers, 'measureparticipant')
    other = account_id(client, admin_headers, 'measureother')
    url = f'/api/admin/accounts/{target}/research-id'
    assert client.put(url, headers=user_headers, json={'research_id': 'P001'}).status_code == 403
    assert client.put(url, headers=admin_headers, json={'research_id': 'P 001'}).status_code == 422
    response = client.put(url, headers=admin_headers, json={'research_id': 'P001'})
    assert response.status_code == 200 and response.json()['research_id'] == 'P001'
    duplicate = client.put(f'/api/admin/accounts/{other}/research-id', headers=admin_headers, json={'research_id': 'P001'})
    assert duplicate.status_code == 409
    assert client.put(url, headers=admin_headers, json={'research_id': 'P002'}).json()['research_id'] == 'P002'
    assert client.put(url, headers=admin_headers, json={'research_id': None}).json()['research_id'] is None
    assert client.put(f'/api/admin/accounts/{other}/research-id', headers=admin_headers,
                      json={'research_id': 'P001'}).status_code == 200


def test_export_has_research_id_codes_and_every_item(client, register, admin_headers):
    headers = register('measureexport')
    client.put(f"/api/admin/accounts/{account_id(client, admin_headers, 'measureexport')}/research-id",
               headers=admin_headers, json={'research_id': 'P042'})
    measure(client, headers, on='2020-02-01')
    latest = measure(client, headers, on='2020-02-02')
    client.put(f"{URL}/{latest['id']}", headers=headers,
               json={'local_date': '2020-02-02', 'expected_revision': 1, 'answers': answers()})
    assert client.get(f'/api/admin/assessments/measurements.csv', headers=headers).status_code == 403

    response = client.get('/api/admin/assessments/measurements.csv?query=P042', headers=admin_headers)
    assert response.status_code == 200, response.text
    current, previous = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert current['research_id'] == 'P042' and current['record_date'] == '2020-02-02'
    assert current['questionnaire_version'] == 'M1'
    assert current['revision_no'] == '2' and current['revision_count'] == '1'
    assert current['Q05_sleep_hours'] == '6.5' and current['Q06_sleep_quality'] == '2'
    assert [current[k] for k in ('D01_completion', 'D02_activity_level', 'D03_overall_mood')] == ['4', '3', '5']
    assert current['D12_lonely'] == '4'
    assert [current[k] for k in ('D13_vigorous_count', 'D14_vigorous_minutes', 'D17_light_count',
                                 'D18_light_minutes')] == ['1', '0', '2', '30']
    assert previous['record_date'] == '2020-02-01'
    one_day = client.get('/api/admin/assessments/measurements.csv?query=P042&start=2020-02-02', headers=admin_headers)
    assert len(list(csv.DictReader(io.StringIO(one_day.content.decode('utf-8-sig'))))) == 1


def test_deleting_an_account_removes_measurements_and_frees_its_research_id(client, register, admin_headers, db_sessionmaker):
    saved = measure(client, register('measuredeleted'))
    target = account_id(client, admin_headers, 'measuredeleted')
    client.put(f'/api/admin/accounts/{target}/research-id', headers=admin_headers, json={'research_id': 'P900'})
    assert client.delete(f'/api/admin/accounts/{target}', headers=admin_headers).status_code == 204
    register('measurereuse')
    reuse = account_id(client, admin_headers, 'measurereuse')
    assert client.put(f'/api/admin/accounts/{reuse}/research-id', headers=admin_headers,
                      json={'research_id': 'P900'}).status_code == 200

    async def remaining():
        async with db_sessionmaker() as db:
            return (await db.scalar(select(func.count(DailyMeasurement.id)).where(DailyMeasurement.id == saved['id'])),
                    await db.scalar(select(func.count(DailyMeasurementRevision.id)).where(
                        DailyMeasurementRevision.measurement_id == saved['id'])))
    assert asyncio.run(remaining()) == (0, 0)


def test_admin_sees_each_users_measurement_status_and_answers(client, register, admin_headers):
    from app.routes.admin_assessments import _today
    today = _today().isoformat()
    headers = register('measureseen')
    target = account_id(client, admin_headers, 'measureseen')
    client.put(f'/api/admin/accounts/{target}/research-id', headers=admin_headers, json={'research_id': '321'})
    measure(client, headers, on='2020-04-01')
    latest = measure(client, headers, on=today, lonely=2)
    register('measureidle')
    assert client.get('/api/admin/assessments/measurements/users', headers=headers).status_code == 403

    page = client.get('/api/admin/assessments/measurements/users?query=321', headers=admin_headers).json()
    assert page['total'] == 1 and page['today'] == today and page['today_count'] == 1
    [seen] = page['items']
    assert seen['research_id'] == '321' and seen['today_completed'] is True
    assert seen['day_count'] == 2 and seen['last_date'] == today
    idle = client.get('/api/admin/assessments/measurements/users?query=measureidle', headers=admin_headers).json()
    assert idle['items'][0]['today_completed'] is False and idle['items'][0]['day_count'] == 0

    days = client.get(f"/api/admin/assessments/measurements/users/{seen['subject_id']}", headers=admin_headers).json()
    assert [item['local_date'] for item in days['items']] == [today, '2020-04-01'] and days['total'] == 2
    assert days['items'][0]['id'] == latest['id'] and days['items'][0]['answers']['lonely'] == 2
    assert client.get('/api/admin/assessments/measurements/users/nobody', headers=admin_headers).status_code == 404
