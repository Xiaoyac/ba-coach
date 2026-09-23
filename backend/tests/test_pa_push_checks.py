"""Persistent diagnostics with synthetic accounts and intercepted transport only."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update, delete, func
from app.pa_push_checks import schedule_check, tick_checks, record_receipt
from app.push_schema import checks, devices
from app.models import AuthSession
from app.db import get_db
from app.identity import require_caller
from app.routes import push as route
from test_pa_push import setup, AT

CALLER=SimpleNamespace(subject_id='u',session=SimpleNamespace(id=1))


async def enqueue(factory, at=AT):
    async with factory() as db:return await schedule_check(db,CALLER,'device',at=at)


@pytest.mark.asyncio
async def test_durable_delay_restart_and_no_page_or_llm_needed(setup):
    row=await enqueue(setup);calls=[]
    await tick_checks(setup,at=AT+timedelta(seconds=59),sender=lambda *a:calls.append(a) or 201)
    assert not calls
    await tick_checks(setup,at=AT+timedelta(seconds=60),sender=lambda *a:calls.append(a) or 201)
    await tick_checks(setup,at=AT+timedelta(seconds=90),sender=lambda *a:calls.append(a) or 201)
    assert len(calls)==1
    payload=calls[0][1]
    assert payload['check_id']==row['id'] and payload['kind']=='delivery_check'
    async with setup() as db:
        state=(await db.execute(select(checks))).mappings().one()
        assert state['state']=='accepted' and state['displayed_at'] is None
        assert payload['receipt_token']!=state['receipt_digest']
        await record_receipt(db,row['id'],payload['receipt_token'],False,at=AT+timedelta(seconds=65))
        # Replays cannot change whether a page was open.
        await record_receipt(db,row['id'],payload['receipt_token'],True,at=AT+timedelta(seconds=66))
        state=(await db.execute(select(checks))).mappings().one()
        assert state['displayed_at'] and state['had_open_window'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['logout','unsubscribe','expired'])
async def test_revocation_and_expiry_no_send(setup,change):
    await enqueue(setup)
    async with setup() as db:
        if change=='logout':await db.execute(delete(AuthSession))
        if change=='unsubscribe':await db.execute(delete(devices))
        await db.commit()
    calls=[]
    await tick_checks(setup,at=AT+timedelta(minutes=12 if change=='expired' else 1),sender=lambda *a:calls.append(a) or 201)
    assert not calls
    async with setup() as db:assert await db.scalar(select(checks.c.state))==('expired' if change=='expired' else 'cancelled')


@pytest.mark.asyncio
async def test_quota_and_wrong_device_or_session(setup):
    async with setup() as db:
        for caller,device in [(CALLER,'wrong'),(SimpleNamespace(subject_id='u',session=SimpleNamespace(id=99)),'device')]:
            with pytest.raises(HTTPException) as exc:await schedule_check(db,caller,device,at=AT)
            assert exc.value.status_code==404
    await enqueue(setup)
    with pytest.raises(HTTPException) as exc:await enqueue(setup,AT+timedelta(seconds=299))
    assert exc.value.status_code==429
    for minutes in [5,10,15,20]:await enqueue(setup,AT+timedelta(minutes=minutes))
    with pytest.raises(HTTPException) as exc:await enqueue(setup,AT+timedelta(minutes=25))
    assert exc.value.status_code==429


@pytest.mark.asyncio
async def test_concurrent_claim_once_and_unknown_never_retried(setup):
    await enqueue(setup);calls=[]
    await asyncio.gather(*(tick_checks(setup,at=AT+timedelta(minutes=1),sender=lambda *a:calls.append(a)) for _ in range(2)))
    await tick_checks(setup,at=AT+timedelta(minutes=2),sender=lambda *a:calls.append(a))
    assert len(calls)==1
    async with setup() as db:assert await db.scalar(select(checks.c.state))=='unknown'


@pytest.mark.asyncio
async def test_receipt_no_auth_but_unforgeable_and_expiring(setup):
    row=await enqueue(setup);calls=[]
    await tick_checks(setup,at=AT+timedelta(minutes=1),sender=lambda *a:calls.append(a) or 201)
    token=calls[0][1]['receipt_token']
    async with setup() as db:
        for ident,key,at in [(row['id'],'bad',AT+timedelta(minutes=1)),('nope',token,AT+timedelta(minutes=1)),
                             (row['id'],token,AT+timedelta(minutes=12))]:
            with pytest.raises(HTTPException) as exc:await record_receipt(db,ident,key,False,at=at)
            assert exc.value.status_code==404
        assert await db.scalar(select(checks.c.displayed_at)) is None


@pytest.mark.asyncio
async def test_api_ownership_receipt_validation_and_no_secret_reads(setup,monkeypatch):
    app=FastAPI();app.include_router(route.router,prefix='/api')
    async def dep():
        async with setup() as db:yield db
    app.dependency_overrides[get_db]=dep
    monkeypatch.setattr(route,'available',lambda:True)
    import app.pa_push_checks as module
    monkeypatch.setattr(module,'now',lambda:AT)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.post('/api/push/checks',json={'device_id':'device'})).status_code==401
        assert (await client.get('/api/push/checks')).status_code==401
        app.dependency_overrides[require_caller]=lambda:CALLER
        result=await client.post('/api/push/checks',json={'device_id':'device'})
        assert result.status_code==202
        calls=[];await tick_checks(setup,at=AT+timedelta(minutes=1),sender=lambda *a:calls.append(a) or 201)
        result=await client.get('/api/push/checks')
        assert len(result.json()['items'])==1 and 'receipt' not in result.text and 'endpoint' not in result.text
        app.dependency_overrides[require_caller]=lambda:SimpleNamespace(subject_id='other',session=SimpleNamespace(id=2))
        assert (await client.get('/api/push/checks')).json()['items']==[]
        assert (await client.post('/api/push/checks',json={'device_id':'device'})).status_code==404
        app.dependency_overrides.pop(require_caller)
        payload={'check_id':calls[0][1]['check_id'],'token':calls[0][1]['receipt_token'],'had_open_window':False}
        assert (await client.post('/api/push/checks/receipt',json=payload)).status_code==204
        assert (await client.post('/api/push/checks/receipt',json={**payload,'token':'x'*43})).status_code==404
        assert (await client.post('/api/push/checks/receipt',json={**payload,'had_open_window':'false'})).status_code==422


@pytest.mark.asyncio
@pytest.mark.parametrize('delay',[1,5,30,60,600])
async def test_selected_delay_is_durable_and_expiry_starts_at_due_time(setup,delay):
    async with setup() as db:
        row=await schedule_check(db,CALLER,'device',delay_seconds=delay,at=AT)
    from app.pa_schedule import utc
    assert datetime.fromisoformat(row['due_at']) == utc(AT)+timedelta(seconds=delay)
    assert datetime.fromisoformat(row['expires_at']) == utc(AT)+timedelta(seconds=delay+600)
    calls=[]
    await tick_checks(setup,at=AT+timedelta(seconds=delay-1),sender=lambda *a:calls.append(a) or 201)
    assert not calls
    await tick_checks(setup,at=AT+timedelta(seconds=delay),sender=lambda *a:calls.append(a) or 201)
    await tick_checks(setup,at=AT+timedelta(seconds=delay+1),sender=lambda *a:calls.append(a) or 201)
    assert len(calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('delay',[0,-1,601,1.5,'5',True,None])
async def test_delay_rejected_by_api_before_enqueue(setup,monkeypatch,delay):
    app=FastAPI();app.include_router(route.router,prefix='/api')
    async def dep():
        async with setup() as db:yield db
    app.dependency_overrides[get_db]=dep
    app.dependency_overrides[require_caller]=lambda:CALLER
    monkeypatch.setattr(route,'available',lambda:True)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.post('/api/push/checks',json={'device_id':'device','delay_seconds':delay})).status_code==422
    async with setup() as db:
        assert await db.scalar(select(func.count()).select_from(checks))==0


@pytest.mark.asyncio
async def test_api_passes_selected_delay(setup,monkeypatch):
    app=FastAPI();app.include_router(route.router,prefix='/api')
    async def dep():
        async with setup() as db:yield db
    app.dependency_overrides[get_db]=dep
    app.dependency_overrides[require_caller]=lambda:CALLER
    monkeypatch.setattr(route,'available',lambda:True)
    import app.pa_push_checks as module
    monkeypatch.setattr(module,'now',lambda:AT)
    from app.pa_schedule import utc
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/push/checks',json={'device_id':'device','delay_seconds':5})
        assert response.status_code==202
        assert datetime.fromisoformat(response.json()['due_at'])==utc(AT)+timedelta(seconds=5)


@pytest.mark.asyncio
async def test_connection_failure_is_visible_without_retry_or_secret_leak(setup):
    from requests.exceptions import ConnectTimeout
    await enqueue(setup);calls=[]
    def fail(*args):
        calls.append(args)
        raise ConnectTimeout('sensitive endpoint must never be exposed')
    await tick_checks(setup,at=AT+timedelta(minutes=1),sender=fail)
    await tick_checks(setup,at=AT+timedelta(minutes=2),sender=fail)
    assert len(calls)==1
    async with setup() as db:
        row=(await db.execute(select(checks))).mappings().one()
        assert row['state']=='unreachable' and row['http_status'] is None and row['displayed_at'] is None
        from app.pa_push_checks import public_check
        assert 'sensitive' not in str(public_check(row))


@pytest.mark.asyncio
async def test_total_send_timeout_settles_but_accepts_late_valid_receipt(setup,monkeypatch):
    import app.pa_push_checks as module
    monkeypatch.setattr(module,'CHECK_SEND_TIMEOUT_SECONDS',0.01)
    calls=[]
    async def slow_transport(fn,*args):
        calls.append(args)
        await asyncio.sleep(1)
        return 201
    monkeypatch.setattr(module.asyncio,'to_thread',slow_transport)
    row=await enqueue(setup)
    await tick_checks(setup,at=AT+timedelta(minutes=1))
    await tick_checks(setup,at=AT+timedelta(minutes=2))
    assert len(calls)==1
    async with setup() as db:
        assert await db.scalar(select(checks.c.state))=='timeout'
        await record_receipt(db,row['id'],calls[0][1]['receipt_token'],False,at=AT+timedelta(minutes=2))
        assert await db.scalar(select(checks.c.displayed_at)) is not None
