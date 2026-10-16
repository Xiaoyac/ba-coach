"""Email retirement preserves password access and historical email data."""
import asyncio
import smtplib
from datetime import timedelta
import pytest
from sqlalchemy import select, func
from invitation_helpers import with_test_invitation
from app.models import AccountEmail, AccountEmailToken, UserAccount, _utcnow
from app.security import hash_token

PASSWORD='correct-horse-battery'
def register(client, **extra):
    payload=dict(username='emailfree',password=PASSWORD,nickname='小河',birth_year=1998)
    response=client.post('/api/auth/register',json=with_test_invitation(client,{**payload,**extra}))
    assert response.status_code==201,response.text
    return response.json()

@pytest.fixture(autouse=True)
def forbid_mail(monkeypatch):
    def unexpected(*args,**kwargs): raise AssertionError('Email transport must not be called')
    monkeypatch.setattr(smtplib,'SMTP',unexpected);monkeypatch.setattr(smtplib,'SMTP_SSL',unexpected)
    monkeypatch.setenv('SMTP_HOST','smtp.example.invalid');monkeypatch.setenv('SMTP_FROM_EMAIL','test@example.invalid')

@pytest.mark.parametrize('legacy_email',[None,'old-client@example.invalid'])
def test_registration_without_email_and_legacy_client_field(client,db_sessionmaker,legacy_email):
    response=register(client,**({'email':legacy_email} if legacy_email else {}));account=response['account']
    assert account['email'] is None and not account['email_required'] and not account['email_delivery_available']
    async def counts():
        async with db_sessionmaker() as db:
            return (await db.scalar(select(func.count()).select_from(AccountEmail)),await db.scalar(select(func.count()).select_from(AccountEmailToken)))
    assert asyncio.get_event_loop().run_until_complete(counts())==(0,0)
    login=client.post('/api/auth/login',json={'username':'emailfree','password':PASSWORD})
    assert login.status_code==200 and not login.json()['account']['email_required']
    me=client.get('/api/auth/me',headers={'Authorization':'Bearer '+response['token']})
    assert me.status_code==200 and not me.json()['email_required']

ROUTES=[('put','/email'),('post','/email/verification/resend'),('post','/email/verify'),('post','/password/forgot'),('post','/password/reset')]
def test_legacy_email_and_tokens_unchanged_and_password_login_works(client,db_sessionmaker):
    response=register(client)
    async def seed():
        async with db_sessionmaker() as db:
            account=await db.scalar(select(UserAccount).where(UserAccount.username=='emailfree'))
            db.add(AccountEmail(account_id=account.id,email='legacy@example.com',normalized_email='legacy@example.com',verified_at=_utcnow()))
            for purpose in ['verify_email','password_reset']:
                db.add(AccountEmailToken(account_id=account.id,purpose=purpose,token_hash=hash_token(purpose+'-old-token'),expires_at=_utcnow()+timedelta(hours=1)))
            await db.commit()
    async def snapshot():
        async with db_sessionmaker() as db:
            emails=(await db.execute(select(AccountEmail))).scalars().all();tokens=(await db.execute(select(AccountEmailToken))).scalars().all()
            return ([(x.account_id,x.email,x.normalized_email,x.verified_at) for x in emails],[(x.id,x.token_hash,x.consumed_at,x.expires_at) for x in tokens])
    loop=asyncio.get_event_loop();loop.run_until_complete(seed());before=loop.run_until_complete(snapshot())
    login=client.post('/api/auth/login',json={'username':'emailfree','password':PASSWORD});assert login.status_code==200
    account=login.json()['account'];assert account['email']=='legacy@example.com' and account['email_verified'] and not account['email_required']
    for method,path in ROUTES:
        assert getattr(client,method)('/api/auth'+path,headers={'Authorization':'Bearer '+response['token']},json={'email':'new@example.invalid','token':'password_reset-old-token','new_password':'cannot-change-this'}).status_code==404
    assert loop.run_until_complete(snapshot())==before
    assert client.post('/api/auth/login',json={'username':'emailfree','password':PASSWORD}).status_code==200

@pytest.mark.parametrize('method,path',ROUTES)
def test_email_routes_absent_from_schema_and_unauthenticated_requests(client,method,path):
    assert '/api/auth'+path not in client.get('/openapi.json').json()['paths']
    assert getattr(client,method)('/api/auth'+path,json={}).status_code==404

def test_logged_in_password_change_keeps_current_password_check(client):
    response=register(client);headers={'Authorization':'Bearer '+response['token']}
    assert client.post('/api/auth/password',json={'current_password':PASSWORD,'new_password':'new-safe-password'}).status_code==401
    assert client.post('/api/auth/password',headers=headers,json={'current_password':'wrong','new_password':'new-safe-password'}).status_code==403
    assert client.post('/api/auth/password',headers=headers,json={'current_password':PASSWORD,'new_password':'new-safe-password'}).status_code==204
    assert client.post('/api/auth/login',json={'username':'emailfree','password':PASSWORD}).status_code==401
    assert client.post('/api/auth/login',json={'username':'emailfree','password':'new-safe-password'}).status_code==200

def test_invitation_password_and_birthday_requirements_remain(client):
    payload=dict(username='emailfree',password=PASSWORD,nickname='小河',birth_year=1998)
    assert client.post('/api/auth/register',json=payload).status_code==422
    for field,value in [('password','short'),('birth_year',None)]:
        assert client.post('/api/auth/register',json=with_test_invitation(client,{**payload,field:value})).status_code==422
    register(client)
    assert client.post('/api/auth/register',json=with_test_invitation(client,payload)).status_code==409
