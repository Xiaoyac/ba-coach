"""Authenticated per-device opt-in. No endpoint/key/token returned by reads."""
import hashlib
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, insert, delete, func
from sqlalchemy.exc import IntegrityError
from ..db import get_db
from ..identity import require_caller, CallerIdentity
from ..config import get_settings
from ..push_schema import devices, deliveries, checks
from ..pa_push_checks import public_check, schedule_check, record_receipt
from ..pa_push import available, validate_subscription, candidates, active_devices, preference_block
from ..pa_schedule import utc
from ..v2_repository import now

router = APIRouter(prefix='/push', tags=['push'])


class Keys(BaseModel):
    model_config = ConfigDict(extra='forbid')
    p256dh: str = Field(min_length=80, max_length=128)
    auth: str = Field(min_length=20, max_length=64)


class Subscription(BaseModel):
    model_config = ConfigDict(extra='forbid')
    endpoint: str = Field(min_length=20, max_length=2048)
    keys: Keys
    expirationTime: int | None = None


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    device_id: str = Field(min_length=1, max_length=36)


class Receipt(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    check_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    token: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    had_open_window: bool | None = None


def require_available():
    if not available(): raise HTTPException(503, '推送服务尚未启用，当前不会发送提醒。')


@router.get('/status')
async def status(caller: CallerIdentity = Depends(require_caller), db=Depends(get_db)):
    if not available(): return {'available': False, 'devices': [], 'upcoming': []}
    current = now()
    rows = await active_devices(db, current, caller.subject_id)
    upcoming = await candidates(db, caller.subject_id, current)
    return {'available': True, 'public_key': get_settings().pa_push_vapid_public_key,
        'buffer_minutes': 15,
        'preference_blocked': await preference_block(db, caller.subject_id),
        'devices': [{'id': d['id'], 'this_session': d['session_id'] == caller.session.id} for d in rows],
        'upcoming': [{'goal_id': c['goal_id'], 'start_at': c['start_at'].isoformat(), 'due_at': c['due_at'].isoformat()}
            for c in upcoming if c['due_at'] > utc(current)][:20]}


@router.post('/subscriptions', status_code=201)
async def subscribe(payload: Subscription, caller: CallerIdentity = Depends(require_caller), db=Depends(get_db)):
    require_available()
    if await preference_block(db, caller.subject_id):
        raise HTTPException(409, '你的档案选择了不主动提醒。若想开启，请先在「我的档案」调整提醒偏好。')
    try: validate_subscription(payload.endpoint, payload.keys.p256dh, payload.keys.auth)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    digest = hashlib.sha256(payload.endpoint.encode()).hexdigest()
    existing = (await db.execute(select(devices).where(devices.c.endpoint_hash == digest))).mappings().one_or_none()
    if existing:
        if existing['user_id'] == caller.subject_id and existing['session_id'] == caller.session.id:
            return {'id': existing['id']}
        raise HTTPException(409, '订阅已绑定其他登录状态，请在此浏览器关闭通知后重新开启。')
    # Bound cost/storage per account. Subscription registration never sends a notification.
    count = await db.scalar(select(func.count()).select_from(devices).where(devices.c.user_id == caller.subject_id))
    if count >= 10: raise HTTPException(409, '通知设备已达上限，请先关闭不再使用的设备。')
    ident = str(uuid.uuid4())
    try:
        await db.execute(insert(devices), {'id': ident, 'user_id': caller.subject_id, 'session_id': caller.session.id,
            'endpoint_hash': digest, 'endpoint': payload.endpoint, 'p256dh': payload.keys.p256dh,
            'auth': payload.keys.auth, 'enabled': True, 'enabled_at': now(), 'updated_at': now()})
        await db.commit()
    except IntegrityError as exc:
        await db.rollback(); raise HTTPException(409, '订阅状态已改变，请重试。') from exc
    return {'id': ident}


@router.delete('/subscriptions/{device_id}', status_code=204)
async def unsubscribe(device_id: str, caller: CallerIdentity = Depends(require_caller), db=Depends(get_db)):
    require_available()
    await db.execute(delete(devices).where(devices.c.id == device_id, devices.c.user_id == caller.subject_id))
    await db.commit()


@router.delete('/subscriptions', status_code=204)
async def unsubscribe_all(caller: CallerIdentity = Depends(require_caller), db=Depends(get_db)):
    require_available()
    await db.execute(delete(devices).where(devices.c.user_id == caller.subject_id))
    await db.commit()


@router.get('/history')
async def history(caller: CallerIdentity = Depends(require_caller), db=Depends(get_db)):
    require_available()
    rows = (await db.execute(select(deliveries.c.due_at, deliveries.c.state, deliveries.c.http_status)
        .where(deliveries.c.user_id == caller.subject_id).order_by(deliveries.c.created_at.desc()).limit(30))).mappings()
    return {'items': [{'due_at': utc(r['due_at']).isoformat(), 'state': r['state'], 'http_status': r['http_status']} for r in rows]}


@router.post('/checks', status_code=202)
async def create_check(payload: CheckRequest, caller: CallerIdentity = Depends(require_caller), db=Depends(get_db)):
    require_available()
    return await schedule_check(db, caller, payload.device_id)


@router.get('/checks')
async def recent_checks(caller: CallerIdentity = Depends(require_caller), db=Depends(get_db)):
    require_available()
    rows = (await db.execute(select(checks).where(checks.c.user_id == caller.subject_id)
        .order_by(checks.c.created_at.desc()).limit(5))).mappings().all()
    return {'items': [public_check(row) for row in rows]}


@router.post('/checks/receipt', status_code=204)
async def receipt(payload: Receipt, db=Depends(get_db)):
    # A closed-page service worker does not have the login token. This narrowly
    # scoped short-lived proof is sent only inside that device's encrypted push.
    require_available()
    await record_receipt(db, payload.check_id, payload.token, payload.had_open_window)
