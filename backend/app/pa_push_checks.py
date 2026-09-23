"""Durable opt-in closed-page checks; no browser timers or synthetic goals.

Transport acceptance and browser display acknowledgement are distinct. A receipt
is proof of executing showNotification, not proof a person saw a notification.
"""
import asyncio
import hashlib
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, insert, update, delete, func
from sqlalchemy.exc import IntegrityError
from requests.exceptions import ConnectTimeout, ConnectionError

from .models import UserAccount
from .push_schema import checks, devices
from .pa_push import active_devices, preference_block, send_push
from .pa_schedule import utc, naive_utc
from .v2_repository import now

CHECK_SEND_TIMEOUT_SECONDS = 12


def public_check(row):
    return {key: (utc(row[key]).isoformat() if row[key] is not None and key.endswith('_at') else row[key])
        for key in ('id', 'device_id', 'state', 'due_at', 'expires_at', 'displayed_at', 'had_open_window', 'http_status')}


async def schedule_check(db, caller, device_id, *, delay_seconds=60, at=None):
    if type(delay_seconds) is not int or not 1 <= delay_seconds <= 600:
        raise HTTPException(422, '测试延迟需为 1–600 秒的整数。')
    current = utc(at or now())
    # Serialize account quotas on production MySQL before the first snapshot read.
    owner = await db.scalar(select(UserAccount.id).where(UserAccount.profile_uuid == caller.subject_id).with_for_update())
    if owner is None: raise HTTPException(404, '通知设备不可用。')
    rows = await active_devices(db, current, caller.subject_id)
    if not any(d['id'] == device_id and d['session_id'] == caller.session.id for d in rows):
        raise HTTPException(404, '请先在此浏览器开启提醒。')
    if await preference_block(db, caller.subject_id):
        raise HTTPException(409, '你的档案选择了不主动提醒，请先调整提醒偏好。')
    recent = await db.scalar(select(checks.c.created_at).where(checks.c.user_id == caller.subject_id)
        .order_by(checks.c.created_at.desc()).limit(1))
    count = await db.scalar(select(func.count()).select_from(checks).where(
        checks.c.user_id == caller.subject_id, checks.c.created_at > naive_utc(current - timedelta(days=1))))
    if (recent and utc(recent) > current - timedelta(minutes=5)) or count >= 5:
        raise HTTPException(429, '请隔5分钟再测试，每24小时最多5次。')
    # Deterministic bucket also protects duplicate concurrent clicks on SQLite.
    ident = hashlib.sha256(f'{caller.subject_id}|{int(current.timestamp()) // 300}'.encode()).hexdigest()
    due = current + timedelta(seconds=delay_seconds)
    row = {'id': ident, 'user_id': caller.subject_id, 'device_id': device_id, 'state': 'queued',
        'due_at': naive_utc(due), 'expires_at': naive_utc(due + timedelta(minutes=10)),
        'created_at': naive_utc(current), 'displayed_at': None, 'had_open_window': None, 'http_status': None}
    try:
        await db.execute(insert(checks), row); await db.commit()
    except IntegrityError as exc:
        await db.rollback(); raise HTTPException(429, '测试已经预约，请稍后查看结果。') from exc
    return public_check(row)


async def record_receipt(db, ident, token, had_open_window, *, at=None):
    current = utc(at or now())
    row = (await db.execute(select(checks).where(checks.c.id == ident))).mappings().one_or_none()
    digest = hashlib.sha256(token.encode()).hexdigest()
    if (not row or not row['receipt_digest'] or not secrets.compare_digest(row['receipt_digest'], digest)
            or utc(row['expires_at']) <= current or row['state'] not in {'attempting', 'accepted', 'unknown', 'unreachable', 'timeout'}):
        raise HTTPException(404, '确认凭据无效或已过期。')
    if not any(d['id'] == row['device_id'] for d in await active_devices(db, current, row['user_id'])):
        raise HTTPException(404, '确认凭据无效或已过期。')
    # First receipt wins; replay cannot turn an open-page receipt into closed-page proof.
    await db.execute(update(checks).where(checks.c.id == ident, checks.c.displayed_at.is_(None)).values(
        displayed_at=naive_utc(current), had_open_window=had_open_window))
    await db.commit()


async def tick_checks(sessionmaker, *, at=None, sender=send_push):
    current = utc(at or now()); counts = {'accepted': 0, 'failed': 0, 'cancelled': 0}
    async with sessionmaker() as db:
        await db.execute(update(checks).where(checks.c.state == 'queued', checks.c.expires_at <= naive_utc(current)).values(state='expired'))
        await db.execute(update(checks).where(checks.c.state == 'attempting',
            checks.c.due_at < naive_utc(current - timedelta(minutes=2))).values(state='unknown'))
        await db.execute(delete(checks).where(checks.c.created_at < naive_utc(current - timedelta(days=7))))
        await db.execute(delete(checks).where(~checks.c.user_id.in_(select(UserAccount.profile_uuid))))
        await db.commit()
        pending = (await db.execute(select(checks).where(checks.c.state == 'queued',
            checks.c.due_at <= naive_utc(current)).order_by(checks.c.due_at).limit(20))).mappings().all()
    for row in pending:
        send_at = current if at is not None else utc(now())
        if send_at >= utc(row['expires_at']): continue
        token = secrets.token_urlsafe(32)
        async with sessionmaker() as db:
            active = await active_devices(db, send_at, row['user_id'])
            device = next((dict(d) for d in active if d['id'] == row['device_id']), None)
            cancelled = device is None or await preference_block(db, row['user_id'])
            claim = await db.execute(update(checks).where(checks.c.id == row['id'], checks.c.state == 'queued').values(
                state='cancelled' if cancelled else 'attempting', receipt_digest=hashlib.sha256(token.encode()).hexdigest()))
            await db.commit()
            if claim.rowcount != 1: continue
        if cancelled:
            counts['cancelled'] += 1; continue
        payload = {'kind': 'delivery_check', 'subscription_id': device['id'], 'tag': row['id'],
            'expires_at': int(utc(row['expires_at']).timestamp() * 1000), 'check_id': row['id'], 'receipt_token': token}
        connection_failed = False
        send_timed_out = False
        try:
            # requests' timeout applies per connection/address, not the whole
            # operation. Bound user-visible waiting across DNS/address retries.
            # A timed-out thread may finish later; never resend or claim failure
            # proves non-delivery. A valid browser receipt can still settle it.
            code = await asyncio.wait_for(asyncio.to_thread(sender, device, payload,
                max(1, int((utc(row['expires_at']) - send_at).total_seconds()))), timeout=CHECK_SEND_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            code = None
            send_timed_out = True
        except (ConnectTimeout, ConnectionError):
            code = None
            connection_failed = True
        except Exception:
            code = None  # Never expose endpoint/key/payload in logs.
        state = ('timeout' if send_timed_out else 'unreachable' if connection_failed else
                 'accepted' if code is not None and 200 <= code < 300 else 'unknown' if code is None else 'failed')
        async with sessionmaker() as db:
            await db.execute(update(checks).where(checks.c.id == row['id']).values(state=state, http_status=code))
            if code in {404, 410}: await db.execute(delete(devices).where(devices.c.id == device['id']))
            await db.commit()
        counts['accepted' if state == 'accepted' else 'failed'] += 1
    return counts
