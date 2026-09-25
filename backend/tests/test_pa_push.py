"""Isolated synthetic data, fake sender: NEVER contacts browser push providers."""
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select, update, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.db import Base, get_db
from app.database_v2_schema import metadata as schema
from app.models import Conversation, ConversationMessage, UserAccount, AuthSession
from app.push_schema import metadata as push_schema, devices, deliveries
from app.pa_schedule import starts, clock, utc
from app.pa_push import tick, candidates, validate_subscription
from app.routes import push as route
from app.identity import require_caller

AT = datetime(2026, 9, 18, 9, 15, tzinfo=timezone.utc)
ANCHOR = AT.replace(hour=1, minute=0)


@pytest.mark.parametrize('text,expected', [
    ('今天下午4点散步15分钟', '2026-09-18T08:00:00+00:00'),
    ('今天16:00散步1小时', '2026-09-18T08:00:00+00:00'),
    ('2026-09-18 16:00散步', '2026-09-18T08:00:00+00:00'),
    ('明天上午十点半', '2026-09-19T02:30:00+00:00'),
    ('下午4点', None), ('今天4点', None), ('今天下午4点左右', None),
    ('今天16:00或者17:00', None), ('今天16:00到17:00', None),
    ('每周五天16:00', None), ('每周三次16:00', None), ('每周一到五16:00', None),
    ('想散步一小时', None), ('今天下午25点', None), ('今天16:99', None),
    ('今天下午4点一刻', None), ('今天上午12点', None), ('每周一和每周三16:00', None),
])
def test_conservative_schedule(text, expected):
    result = starts(text, anchor=ANCHOR, timezone_name='Asia/Shanghai', at=AT)
    assert ([x.isoformat() for x in result] == [expected]) if expected else not result


def test_recurring_days_and_dst():
    result = starts('每周一、三、五下午4点', anchor=ANCHOR, timezone_name='Asia/Shanghai', at=AT)
    assert result and all(x.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).weekday() in {0, 2, 4} for x in result)
    assert len(starts('每天16:00', anchor=ANCHOR, timezone_name='Asia/Shanghai', at=AT)) == 8
    assert not starts('今天16:00', anchor=ANCHOR, timezone_name='wrong', at=AT)
    assert not starts('2026-11-01 01:30', anchor=ANCHOR, timezone_name='America/New_York', at=AT)
    assert not starts('2026-03-08 02:30', anchor=ANCHOR, timezone_name='America/New_York', at=AT)


def valid_keys():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    enc = lambda v: base64.urlsafe_b64encode(v).rstrip(b'=').decode()
    return {'p256dh': enc(key), 'auth': enc(b'0123456789abcdef')}


@pytest.mark.parametrize('endpoint', ['http://fcm.googleapis.com/a', 'https://127.0.0.1/a',
    'https://fcm.googleapis.com.evil.example/a', 'https://user@fcm.googleapis.com/a',
    'https://fcm.googleapis.com:8443/a', 'https://evil.push.apple.com.evil/a',
    'https://localhost/a', 'https://169.254.169.254/latest/meta-data'])
def test_endpoint_ssrf(endpoint):
    with pytest.raises(ValueError): validate_subscription(endpoint, **valid_keys())


@pytest_asyncio.fixture
async def setup(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "push-tests.db"}')
    async with engine.begin() as conn:
        await conn.run_sync(schema.create_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(push_schema.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        await db.execute(insert(schema.tables['user_profile']), [{'uuid':'u'}, {'uuid':'other'}])
        await db.execute(insert(UserAccount), {'id':1, 'username':'test', 'password_hash':'unused', 'profile_uuid':'u'})
        await db.execute(insert(AuthSession), {'id':1, 'account_id':1, 'token_hash':'fake', 'expires_at':AT+timedelta(days=10)})
        await db.execute(insert(Conversation), {'id':1, 'session_id':'synthetic', 'subject_id':'u'})
        await db.execute(insert(ConversationMessage), [
            {'id':1, 'conversation_id':1, 'position':0, 'role':'user', 'content':'今天下午4点散步15分钟', 'created_at':ANCHOR},
            {'id':2, 'conversation_id':1, 'position':1, 'role':'assistant', 'content':'计划已确认', 'created_at':ANCHOR}])
        await db.execute(insert(schema.tables['pa_goals']), {'id':'g', 'user_id':'u', 'title':'散步', 'status':'active', 'created_from_conversation_id':1})
        await db.execute(insert(schema.tables['module_two_record']), {'id':'p', 'goal_id':'g', 'version_no':1,
            'timezone':'Asia/Shanghai', 'record_status':'confirmed', 'confirmation_status':'confirmed',
            'confirmation_message_id':2, 'activity_content':'散步', 'schedule_text':'今天下午4点散步15分钟', 'duration_minutes':15})
        await db.execute(update(schema.tables['pa_goals']).values(current_plan_record_id='p'))
        await db.execute(insert(schema.tables['pa_cycles']), {'id':'c', 'goal_id':'g', 'ordinal':1,
            'status':'waiting_execution', 'module_two_record_id':'p'})
        await db.execute(insert(devices), {'id':'device', 'user_id':'u', 'session_id':1, 'endpoint_hash':'digest',
            'endpoint':'https://fcm.googleapis.com/fake', **valid_keys(), 'enabled':True,
            'enabled_at':ANCHOR.replace(tzinfo=None), 'updated_at':ANCHOR.replace(tzinfo=None)})
        await db.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_buffer_single_send_and_restart(setup):
    calls = []
    sender = lambda *args: calls.append(args) or 201
    async with setup() as db:
        item, = await candidates(db, 'u', AT)
        assert item['due_at'] == item['start_at'] + timedelta(minutes=15, hours=1)
    # The previous end + 15 minutes boundary must no longer send.
    assert (await tick(setup, at=AT-timedelta(minutes=45), sender=sender))['accepted'] == 0
    assert (await tick(setup, at=AT-timedelta(seconds=1), sender=sender))['accepted'] == 0
    assert (await tick(setup, at=AT, sender=sender))['accepted'] == 1
    assert (await tick(setup, at=AT+timedelta(minutes=1), sender=sender))['duplicate'] == 1
    assert len(calls) == 1 and calls[0][2] == 1800
    assert '散步' not in str(calls[0][1]) and 'goal_id' not in calls[0][1]
    async with setup() as db:
        assert await db.scalar(select(deliveries.c.state)) == 'accepted'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['missing_duration','no_clock','paused','plan_replaced','cycle_cancelled','deleted_source','logout','expired','late_subscription','no_subscription','disabled_subscription'])
async def test_cancellation_and_no_guesses(setup, change):
    async with setup() as db:
        plans, goals, cycles = [schema.tables[n] for n in ('module_two_record','pa_goals','pa_cycles')]
        if change=='missing_duration': await db.execute(update(plans).values(duration_minutes=None))
        if change=='no_clock': await db.execute(update(plans).values(schedule_text='晚饭后'))
        if change=='paused': await db.execute(update(goals).values(status='paused'))
        if change=='plan_replaced': await db.execute(update(goals).values(current_plan_record_id=None))
        if change=='cycle_cancelled': await db.execute(update(cycles).values(status='cancelled'))
        if change=='deleted_source': await db.execute(delete(ConversationMessage))
        if change=='logout': await db.execute(delete(AuthSession))
        if change=='expired': await db.execute(update(AuthSession).values(expires_at=AT-timedelta(seconds=1)))
        if change=='late_subscription': await db.execute(update(devices).values(enabled_at=AT.replace(tzinfo=None)))
        if change=='no_subscription': await db.execute(delete(devices))
        if change=='disabled_subscription': await db.execute(update(devices).values(enabled=False))
        await db.commit()
    calls=[]
    assert (await tick(setup, at=AT, sender=lambda *a:calls.append(a) or 201))['accepted']==0
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('activity,when,kind,expected', [('散步','今天','performed',0),('散步','今天','not_performed',0),
    ('散步','昨天','performed',1),('游泳','今天','performed',1),('散步','今天','idea',1)])
async def test_feedback_matches_occurrence(setup, activity, when, kind, expected):
    async with setup() as db:
        await db.execute(insert(ConversationMessage), {'id':3,'conversation_id':1,'position':2,'role':'user',
            'content':f'{when}{activity}', 'created_at':AT-timedelta(minutes=2)})
        await db.execute(insert(schema.tables['pa_activity_events']), {'id':'event','user_id':'u','goal_id':'g','cycle_id':'c',
            'source_conversation_id':1,'source_message_id':3,'event_index':0,'event_kind':kind,
            'activity_content':activity,'occurred_at_text':when,'source_quote':f'{when}{activity}'})
        await db.commit()
    assert (await tick(setup, at=AT, sender=lambda *a:201))['accepted']==expected


@pytest.mark.asyncio
@pytest.mark.parametrize('code,state',[(410,'failed'),(503,'failed'),(None,'unknown')])
async def test_failure_not_retried_and_expired_subscription_removed(setup, code, state):
    calls=[]
    await tick(setup,at=AT,sender=lambda *a:calls.append(a) or code)
    await tick(setup,at=AT+timedelta(seconds=30),sender=lambda *a:calls.append(a) or code)
    assert len(calls)==1
    async with setup() as db:
        assert await db.scalar(select(deliveries.c.state))==state
        assert bool(await db.scalar(select(devices.c.id)))==(code!=410)


@pytest.mark.asyncio
async def test_api_authentication_ownership_and_disabled_rollout(setup,monkeypatch):
    app=FastAPI();app.include_router(route.router,prefix='/api')
    async def db_dependency():
        async with setup() as db: yield db
    app.dependency_overrides[get_db]=db_dependency
    caller=SimpleNamespace(subject_id='u',session=SimpleNamespace(id=1))
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/api/push/status')).status_code==401
        app.dependency_overrides[require_caller]=lambda:caller
        monkeypatch.setattr(route,'available',lambda:False)
        assert (await client.get('/api/push/status')).json()['available'] is False
        assert (await client.post('/api/push/subscriptions',json={'endpoint':'https://fcm.googleapis.com/fake', 'keys':valid_keys()})).status_code==503
        monkeypatch.setattr(route,'available',lambda:True)
        monkeypatch.setattr(route,'now',lambda:AT.replace(tzinfo=None))
        result=await client.get('/api/push/status')
        assert result.status_code==200 and 'endpoint' not in result.text and 'auth' not in result.text
        assert result.json()['buffer_minutes']==60
        caller.subject_id='other'
        assert not (await client.get('/api/push/status')).json()['devices']
        assert (await client.delete('/api/push/subscriptions/device')).status_code==204
        async with setup() as db: assert await db.scalar(select(devices.c.id))=='device'
        body={'endpoint':'https://fcm.googleapis.com/fake','keys':valid_keys()}
        # Existing endpoint belongs to u. No account-transfer or key disclosure.
        async with setup() as db:
            import hashlib
            await db.execute(update(devices).values(endpoint_hash=hashlib.sha256(body['endpoint'].encode()).hexdigest()));await db.commit()
        assert (await client.post('/api/push/subscriptions',json=body)).status_code==409


@pytest.mark.asyncio
async def test_overlapping_workers_claim_once(setup):
    import asyncio
    calls=[]
    await asyncio.gather(tick(setup,at=AT,sender=lambda *a:calls.append(a) or 201),
                         tick(setup,at=AT,sender=lambda *a:calls.append(a) or 201))
    assert len(calls)==1


@pytest.mark.asyncio
async def test_recurring_same_goal_next_day_and_previous_feedback(setup):
    async with setup() as db:
        await db.execute(update(ConversationMessage).where(ConversationMessage.id==1).values(content='每天16:00散步15分钟'))
        await db.execute(update(schema.tables['module_two_record']).values(schedule_text='每天16:00散步15分钟'))
        await db.commit()
    calls=[]
    for when in (AT, AT+timedelta(days=1), AT+timedelta(days=1,minutes=1)):
        await tick(setup,at=when,sender=lambda *a:calls.append(a) or 201)
    assert len(calls)==2


@pytest.mark.asyncio
async def test_offline_expiry_never_catches_up_old_reminders(setup):
    calls=[]
    await tick(setup,at=AT+timedelta(hours=3),sender=lambda *a:calls.append(a) or 201)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('text,expected', [('做完了',0),('今天没做',0),('今天散步推迟到晚上',0),('昨天做完了',1),('今天游泳做完了',1)])
async def test_unstructured_feedback_and_cancellation(setup,text,expected):
    async with setup() as db:
        await db.execute(insert(ConversationMessage), {'id':3,'conversation_id':1,'position':2,'role':'user',
            'content':text,'created_at':AT-timedelta(minutes=2)})
        await db.commit()
    assert (await tick(setup,at=AT,sender=lambda *a:201))['accepted']==expected


@pytest.mark.asyncio
@pytest.mark.parametrize('activity,slot,expected',[('散步','16:00–16:15',0),('散步','08:00–09:00',1),('游泳','16:00–16:15',1)])
async def test_daily_record_activity_and_time_match(setup,activity,slot,expected):
    from app.models import AssessmentEntry, ActivityLog
    async with setup() as db:
        await db.execute(insert(AssessmentEntry), {'id':1,'subject_id':'u','recorded_on':AT.date(),'timezone':'Asia/Shanghai',
            'status':'completed','scale_version':2,'completion_rate':0,'activity_level':0,'overall_mood':0})
        await db.execute(insert(ActivityLog), {'entry_id':1,'position':0,'activity':activity,'time_slot':slot,'emotion':0})
        await db.commit()
    assert (await tick(setup,at=AT,sender=lambda *a:201))['accepted']==expected


@pytest.mark.asyncio
async def test_migration_dry_run_and_idempotent(tmp_path):
    from scripts.migrate_pa_push import migrate
    from sqlalchemy import inspect
    path=str(tmp_path/'migration.db'); url=f'sqlite+aiosqlite:///{path}'
    engine=create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(schema.create_all);await conn.run_sync(Base.metadata.create_all)
    with pytest.raises(ValueError): await migrate(url,'wrong',True)
    await migrate(url,path)
    async with engine.begin() as conn:
        assert 'pa_push_devices' not in await conn.run_sync(lambda c:inspect(c).get_table_names())
    await migrate(url,path,True);await migrate(url,path,True)
    async with engine.begin() as conn:
        assert 'pa_push_devices' in await conn.run_sync(lambda c:inspect(c).get_table_names())
    await engine.dispose()


def test_real_encryption_transport_without_network(tmp_path,monkeypatch):
    import requests
    from scripts.generate_pa_vapid import generate
    from scripts.pa_push_worker import validate_keys
    from app import pa_push
    import scripts.pa_push_worker as worker
    folder=tmp_path/'keys';generate(folder)
    with pytest.raises(SystemExit):generate(folder)
    settings=SimpleNamespace(pa_push_vapid_public_key=(folder/'pa-vapid-public.txt').read_text().strip(),
        pa_push_vapid_private_key_path=str(folder/'pa-vapid-private.pem'),pa_push_vapid_subject='https://test.example/')
    monkeypatch.setattr(pa_push,'get_settings',lambda:settings);monkeypatch.setattr(worker,'get_settings',lambda:settings)
    validate_keys();calls=[]
    def fake_request(self,method,url,**kwargs):
        calls.append(kwargs);r=requests.Response();r.status_code=201;r._content=b'';return r
    monkeypatch.setattr(requests.Session,'request',fake_request)
    code=pa_push.send_push({'endpoint':'https://fcm.googleapis.com/synthetic',**valid_keys()},{'body':'private fixture'},60)
    assert code==201 and len(calls)==1
    assert calls[0]['allow_redirects'] is False and calls[0]['timeout']==10
    assert b'private fixture' not in calls[0]['data']
    assert calls[0]['headers']['content-encoding']=='aes128gcm'


@pytest.mark.asyncio
async def test_recent_turn_defers_until_extraction_window(setup):
    async with setup() as db:
        await db.execute(insert(ConversationMessage),{'id':3,'conversation_id':1,'position':2,'role':'user',
            'content':'我回来聊聊','created_at':AT-timedelta(seconds=10)});await db.commit()
    calls=[]
    assert (await tick(setup,at=AT,sender=lambda *a:calls.append(a) or 201))['accepted']==0
    assert not calls
    assert (await tick(setup,at=AT+timedelta(minutes=2),sender=lambda *a:calls.append(a) or 201))['accepted']==1


@pytest.mark.asyncio
@pytest.mark.parametrize('frequency,slot,expected', [('暂时不需要提醒',None,0),('仅在我主动找你时提醒',None,0),
    ('每天一次','早晨7-9',0),('每天一次','下午14-18',1)])
async def test_existing_profile_preferences_respected(setup,frequency,slot,expected):
    async with setup() as db:
        await db.execute(insert(schema.tables['user_preferences']),{'user_id':'u','reminder_frequency':frequency,'reminder_time_slot':slot});await db.commit()
    assert (await tick(setup,at=AT,sender=lambda *a:201))['accepted']==expected


@pytest.mark.asyncio
async def test_profile_frequency_cross_day_limit(setup):
    async with setup() as db:
        await db.execute(insert(schema.tables['user_preferences']),{'user_id':'u','reminder_frequency':'隔天一次'})
        await db.execute(update(ConversationMessage).where(ConversationMessage.id==1).values(content='每天16:00散步15分钟'))
        await db.execute(update(schema.tables['module_two_record']).values(schedule_text='每天16:00散步15分钟'));await db.commit()
    calls=[]
    for days in (0,1,2):await tick(setup,at=AT+timedelta(days=days),sender=lambda *a:calls.append(a) or 201)
    assert len(calls)==2
