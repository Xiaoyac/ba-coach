import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.invitations import claim_invitation
from app.models import RegistrationInvite, UserAccount
from invitation_helpers import issue_test_invitation
from test_admin_sandbox import sandbox_admin_headers


def payload(code=None, name='inviteuser'):
    result = dict(username=name, password='test-password-123', email=f'{name}@example.com',
                  nickname=name, tag='54321', birth_date='1998-01-01')
    if code is not None:
        result['invitation_code'] = code
    return result


def test_admin_generation_permissions_unique_and_list(client, sandbox_admin_headers, auth_headers):
    endpoint = '/api/admin/invitations'
    for method in (client.get, client.post):
        kwargs = {'json': {'count': 1}} if method == client.post else {}
        assert method(endpoint, **kwargs).status_code == 401
        assert method(endpoint, headers=auth_headers, **kwargs).status_code == 403
    for count in (0, 21, True, '5'):
        assert client.post(endpoint, headers=sandbox_admin_headers, json={'count': count}).status_code == 422
    codes = []
    for _ in range(2):
        response = client.post(endpoint, headers=sandbox_admin_headers, json={'count': 20})
        assert response.status_code == 201
        assert response.headers['cache-control'] == 'no-store'
        rows = response.json()['invitations']
        assert all(row['used_at'] is None for row in rows)
        codes += [row['code'] for row in rows]
    assert len(set(codes)) == 40
    assert all(len(code.replace('-', '')) == 32 for code in codes)
    listed = client.get(endpoint, headers=sandbox_admin_headers)
    assert listed.headers['cache-control'] == 'no-store'
    assert set(codes) <= {row['code'] for row in listed.json()['invitations']}


@pytest.mark.parametrize('code', [None, '', ' ', 'invalid', 'A' * 32])
def test_registration_requires_valid_unused_code(client, code):
    response = client.post('/api/auth/register', json=payload(code))
    assert response.status_code == (400 if code == 'A' * 32 else 422)
    assert client.post('/api/auth/login', json={'username': 'inviteuser', 'password': 'test-password-123'}).status_code == 401


def test_consumed_once_old_codes_never_expire_and_login_unaffected(client, sandbox_admin_headers, db_sessionmaker):
    code = issue_test_invitation(client)
    async def age():
        async with db_sessionmaker() as db:
            invite = await db.scalar(select(RegistrationInvite).where(RegistrationInvite.code == code))
            invite.created_at = datetime(1990, 1, 1, tzinfo=timezone.utc)
            await db.commit()
    client.portal.call(age)
    formatted = '-'.join(code[i:i+8] for i in range(0, 32, 8)).lower()
    response = client.post('/api/auth/register', json=payload(' ' + formatted + ' '))
    assert response.status_code == 201, response.text
    assert client.post('/api/auth/register', json=payload(code, 'secondinviteuser')).status_code == 400
    used = client.get('/api/admin/invitations?used=true', headers=sandbox_admin_headers).json()['invitations']
    row = next(row for row in used if row['code'].replace('-', '') == code)
    assert row['used_by_username'] == 'inviteuser' and row['used_at']
    assert client.post('/api/auth/login', json={'username': 'inviteuser', 'password': 'test-password-123'}).status_code == 200


def test_failed_registration_rolls_claim_back(client, auth_headers):
    code = issue_test_invitation(client)
    response = client.post('/api/auth/register', json=payload(code, 'tester'))
    assert response.status_code == 409
    assert client.post('/api/auth/register', json=payload(code, 'afterfailure')).status_code == 201


async def test_simultaneous_claims_across_connections_and_account_deletion(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/race.db', connect_args={'timeout': 10})
    from sqlalchemy import event
    @event.listens_for(engine.sync_engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    code = 'B' * 32
    async with factory() as db:
        db.add(RegistrationInvite(code=code))
        await db.commit()
    async def attempt(name):
        async with factory() as db:
            try:
                await claim_invitation(db, code)
                await asyncio.sleep(0.05)
                account = UserAccount(username=name, password_hash='test-only', profile_uuid=str(uuid4()))
                db.add(account)
                await db.flush()
                invite = await db.scalar(select(RegistrationInvite).where(RegistrationInvite.code == code))
                invite.used_by_account_id = account.id
                await db.commit()
                return True
            except HTTPException as exc:
                assert exc.status_code == 400
                await db.rollback()
                return False
    try:
        assert sorted(await asyncio.gather(attempt('first'), attempt('second'))) == [False, True]
        async with factory() as db:
            assert len((await db.scalars(select(UserAccount))).all()) == 1
            await db.execute(delete(UserAccount))
            await db.commit()
        async with factory() as db:
            invite = await db.scalar(select(RegistrationInvite))
            assert invite.used_at and invite.used_by_account_id is None
            with pytest.raises(HTTPException):
                await claim_invitation(db, code)
    finally:
        await engine.dispose()
