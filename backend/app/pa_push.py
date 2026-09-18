"""Push transport and scheduler. No LLM, no browser timers, no implicit DDL.

At-most-one attempt per goal/start/device. Network ambiguity is terminal: a
missed notification is preferable to repeated nagging. Accepted != delivered.
"""
import asyncio
import base64
import hashlib
import json
import logging
import re
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import select, insert, update, delete, or_
from sqlalchemy.exc import IntegrityError
from .config import get_settings
from .database_v2_schema import metadata as schema
from .models import AuthSession, UserAccount, Conversation, ConversationMessage, AssessmentEntry, ActivityLog
from .pa_schedule import starts, explicit_day, clock, utc, naive_utc, BUFFER, MAX_LATENESS
from .push_schema import devices, deliveries
from .v2_repository import now

log = logging.getLogger(__name__)


def available():
    s = get_settings()
    return bool(s.pa_push_enabled and s.database_schema_version == 'v2'
        and s.pa_push_vapid_public_key and s.pa_push_vapid_private_key_path and s.pa_push_vapid_subject)


def vapid_subject():
    # py-vapid's strict HTTPS subject validation expects an origin, without '/'.
    return (get_settings().pa_push_vapid_subject or '').rstrip('/')


async def preference_block(db, user_id):
    table = schema.tables['user_preferences']
    value = await db.scalar(select(table.c.reminder_frequency).where(table.c.user_id == user_id))
    return value in {'暂时不需要提醒', '仅在我主动找你时提醒'}


async def preference_allows(db, item, at):
    from zoneinfo import ZoneInfo
    table = schema.tables['user_preferences']
    pref = (await db.execute(select(table).where(table.c.user_id == item['user_id']))).mappings().one_or_none()
    if not pref: return True
    if pref['reminder_frequency'] in {'暂时不需要提醒', '仅在我主动找你时提醒'}: return False
    local = utc(at).astimezone(ZoneInfo(item['timezone']))
    minute = local.hour * 60 + local.minute
    lo, hi = pref['reminder_start_minute'], pref['reminder_end_minute']
    if lo is None or hi is None:
        slots = {'早晨7-9': (420,540), '上午9-12': (540,720), '中午12-14': (720,840),
                 '下午14-18': (840,1080), '傍晚18-21': (1080,1260), '晚上21-23': (1260,1380)}
        lo, hi = slots.get(pref['reminder_time_slot'], (None,None))
    if lo is not None and hi is not None and not ((lo <= minute < hi) if lo < hi else (minute >= lo or minute < hi)):
        return False
    gap = {'每天一次': 1, '隔天一次': 2, '每三天一次': 3, '每周一次': 7}.get(pref['reminder_frequency'])
    if gap:
        previous = await db.scalar(select(deliveries.c.created_at).where(
            deliveries.c.user_id == item['user_id'], deliveries.c.state.in_(['accepted','attempting','unknown']),
            or_(deliveries.c.goal_id != item['goal_id'], deliveries.c.start_at != naive_utc(item['start_at'])))
            .order_by(deliveries.c.created_at.desc()).limit(1))
        if previous and (local.date() - utc(previous).astimezone(ZoneInfo(item['timezone'])).date()).days < gap: return False
    return True


def validate_subscription(endpoint, p256dh, auth):
    """Prevent SSRF: only known HTTPS browser-push authorities, no redirects."""
    parsed = urlsplit(endpoint)
    host = parsed.hostname or ''
    allowed = host in {'fcm.googleapis.com', 'updates.push.services.mozilla.com'} or bool(re.fullmatch(
        r'[a-zA-Z0-9-]+\.(?:push\.apple\.com|notify\.windows\.com)', host))
    if (parsed.scheme != 'https' or not allowed or parsed.port not in {None, 443}
            or parsed.username or parsed.password or parsed.fragment or not parsed.path.startswith('/')
            or len(endpoint) > 2048):
        raise ValueError('此推送服务暂不支持，请使用支持的独立浏览器。')
    try:
        key = base64.urlsafe_b64decode(p256dh + '=' * (-len(p256dh) % 4))
        secret = base64.urlsafe_b64decode(auth + '=' * (-len(auth) % 4))
        from cryptography.hazmat.primitives.asymmetric import ec
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), key)
        if len(key) != 65 or len(secret) != 16: raise ValueError()
    except Exception as exc:
        raise ValueError('推送订阅密钥无效，请重新开启。') from exc


async def active_devices(db, at, user_id=None):
    query = select(devices).join(AuthSession, AuthSession.id == devices.c.session_id).join(
        UserAccount, (UserAccount.id == AuthSession.account_id) & (UserAccount.profile_uuid == devices.c.user_id))
    query = query.where(devices.c.enabled.is_(True), AuthSession.expires_at > utc(at))
    if user_id: query = query.where(devices.c.user_id == user_id)
    return (await db.execute(query)).mappings().all()


async def candidates(db, user_id, at):
    """Only confirmed, current, active plans backed by surviving user evidence."""
    if await preference_block(db, user_id): return []
    goals, plans, cycles = [schema.tables[n] for n in ('pa_goals', 'module_two_record', 'pa_cycles')]
    rows = (await db.execute(select(plans, goals.c.user_id).join(goals,
        (goals.c.current_plan_record_id == plans.c.id) & (goals.c.id == plans.c.goal_id)).where(
        goals.c.user_id == user_id, goals.c.status == 'active', plans.c.record_status == 'confirmed',
        plans.c.confirmation_status == 'confirmed', plans.c.duration_minutes > 0))).mappings().all()
    result = []
    for plan in rows:
        cycle = (await db.execute(select(cycles).where(cycles.c.goal_id == plan['goal_id'])
            .order_by(cycles.c.ordinal.desc()).limit(1))).mappings().one_or_none()
        if not cycle or cycle['module_two_record_id'] != plan['id'] or cycle['status'] not in {'waiting_execution', 'reviewing'}:
            continue
        confirmation = (await db.execute(select(ConversationMessage).join(Conversation).where(
            ConversationMessage.id == plan['confirmation_message_id'], Conversation.subject_id == user_id))).scalar_one_or_none()
        if not confirmation: continue
        messages = (await db.execute(select(ConversationMessage).where(
            ConversationMessage.conversation_id == confirmation.conversation_id,
            ConversationMessage.role == 'user', ConversationMessage.position <= confirmation.position)
            .order_by(ConversationMessage.position.desc()))).scalars().all()
        # An extractor's paraphrase/ISO guess isn't sufficient to trigger contact.
        schedule = (plan['schedule_text'] or '').strip()
        source = next((m for m in messages if schedule and schedule in m.content), None)
        if not source: continue
        frequency = plan['frequency_rule']
        if not re.search(r'每周|每星期|每天|每日', schedule) and isinstance(frequency, dict):
            text = frequency.get('text')
            if text and any(text in m.content for m in messages): schedule = f'{text} {schedule}'
        for start in starts(schedule, anchor=source.created_at, timezone_name=plan['timezone'], at=at):
            due = start + timedelta(minutes=plan['duration_minutes']) + BUFFER
            # Never replay old plans or starts before their explicit confirmation.
            if start < utc(confirmation.created_at) or due < utc(at) - MAX_LATENESS or start > utc(at) + timedelta(days=7):
                continue
            result.append({'goal_id': plan['goal_id'], 'plan_id': plan['id'], 'cycle_id': cycle['id'],
                'user_id': user_id, 'start_at': start, 'due_at': due, 'timezone': plan['timezone'],
                'activity': plan['activity_content'], 'conversation_id': confirmation.conversation_id,
                'confirmation_position': confirmation.position})
    return result


def same_activity(a, b):
    clean = lambda v: re.sub(r'[\s，。！？、,.!?]', '', v or '')
    return bool(clean(a)) and clean(a) == clean(b)


async def feedback_received(db, item):
    """Match this activity AND occurrence, not a whole goal or yesterday's reply."""
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(item['timezone']); day = utc(item['start_at']).astimezone(zone).date()
    events = schema.tables['pa_activity_events']
    rows = (await db.execute(select(events).where(events.c.user_id == item['user_id'],
        events.c.goal_id == item['goal_id'], events.c.status == 'active',
        events.c.event_kind.in_(['performed', 'not_performed'])))).mappings().all()
    for row in rows:
        if not same_activity(row['activity_content'], item['activity']): continue
        message = await db.get(ConversationMessage, row['source_message_id'])
        if not message: continue
        if utc(message.created_at) < utc(item['start_at']): continue
        anchor = utc(message.created_at).astimezone(zone).date()
        text = row['occurred_at_text'] or row['source_quote']
        explicit_time = clock(text)
        start_local = utc(item['start_at']).astimezone(zone)
        if explicit_time and explicit_time != start_local.time().replace(tzinfo=None): continue
        if not explicit_time and re.search(r'早上|早晨|上午', text) and start_local.hour >= 12: continue
        when = explicit_day(text, anchor)
        if when is None and not re.search(r'昨天|前天|上周|上次|以前|之前|明天|后天|\d+月|周[一二三四五六日天]', text):
            when = anchor
        if when == day: return True
    # The daily record is also valid feedback, but only for the same named
    # activity and an overlapping explicit time slot, never the whole day.
    entries = (await db.execute(select(AssessmentEntry).where(AssessmentEntry.subject_id == item['user_id'],
        AssessmentEntry.recorded_on == day, AssessmentEntry.status == 'completed'))).scalars().all()
    for entry in entries:
        logs = (await db.execute(select(ActivityLog).where(ActivityLog.entry_id == entry.id))).scalars().all()
        for record in logs:
            m = re.fullmatch(r'(\d{2}):(\d{2})[–—-](\d{2}):(\d{2})', record.time_slot.strip())
            local = utc(item['start_at']).astimezone(zone)
            minute = local.hour * 60 + local.minute
            if same_activity(record.activity, item['activity']) and m:
                h1, m1, h2, m2 = map(int, m.groups())
                if h1*60+m1 <= minute < h2*60+m2: return True
    # Conservative handling of unstructured, current-chat feedback/corrections
    # before extraction finishes. Do not infer success or write goal state.
    recent = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == item['conversation_id'], ConversationMessage.role == 'user',
        ConversationMessage.position > item['confirmation_position'],
        ConversationMessage.created_at >= utc(item['start_at']) - timedelta(hours=12)))).scalars().all()
    for message in recent:
        text = message.content.strip()
        anchor = utc(message.created_at).astimezone(zone).date()
        if anchor == day and re.search(r'取消|改到|改成|推迟', text) and (
            (item['activity'] and item['activity'] in text) or re.match(r'^(?:我)?(?:今天)?(?:的计划)?(?:取消|改到|改成|推迟)', text)):
            return True
        when = explicit_day(text, anchor)
        if when is None and not re.search(r'昨天|前天|上周|上次|之前|以后|明天|后天', text): when = anchor
        if when != day: continue
        if utc(message.created_at) >= utc(item['start_at']) and re.fullmatch(r'(?:我)?(?:今天)?(?:已经)?(?:做完了|完成了|没做|没去)[。！!]*', text): return True
        if re.fullmatch(r'(?:我)?(?:今天)?(?:不做了|不去了|取消了)[。！!]*', text): return True
        if item['activity'] and item['activity'] in text and re.search(r'取消|改到|改成|推迟|不想|不去|不做|没去|没做|做完|完成了|回来了|回家了|走完', text): return True
    return False


def send_push(device, payload, ttl):
    from pywebpush import webpush, WebPushException
    import requests
    class NoRedirectSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs['allow_redirects'] = False
            return super().request(*args, **kwargs)
    settings = get_settings()
    validate_subscription(device['endpoint'], device['p256dh'], device['auth'])
    with NoRedirectSession() as session:
        try:
            response = webpush(subscription_info={'endpoint': device['endpoint'],
                'keys': {'p256dh': device['p256dh'], 'auth': device['auth']}},
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=settings.pa_push_vapid_private_key_path,
                vapid_claims={'sub': vapid_subject()},
                ttl=ttl, timeout=10, requests_session=session,
                headers={'Urgency': 'normal'})
            return response.status_code
        except WebPushException as exc:
            return exc.response.status_code if exc.response is not None else None


async def tick(sessionmaker, *, at=None, sender=send_push):
    """One worker iteration; DB unique claim also protects overlapping workers."""
    fixed_time = at is not None  # Deterministic test clock; real sends recheck wall time.
    at = utc(at or now()); counts = {'accepted': 0, 'failed': 0, 'suppressed': 0, 'duplicate': 0}
    async with sessionmaker() as db:
        subscribers = await active_devices(db, at)
    by_user = {}
    for device in subscribers: by_user.setdefault(device['user_id'], []).append(dict(device))
    for uid, user_devices in by_user.items():
        async with sessionmaker() as db:
            items = await candidates(db, uid, at)
        for item in items:
            if item['due_at'] > at: continue
            for device in user_devices:
                send_at = at if fixed_time else utc(now())
                if send_at >= item['due_at'] + MAX_LATENESS: continue
                # Enabling notifications late doesn't replay a past activity.
                if utc(device['enabled_at']) > item['start_at']: continue
                key = hashlib.sha256(f"{item['goal_id']}|{item['start_at'].isoformat()}|{device['id']}".encode()).hexdigest()
                async with sessionmaker() as db:
                    # Serialize opt-in frequency checks per account on MySQL.
                    # Lock before the first consistent read in this transaction.
                    owner = await db.scalar(select(UserAccount.id).where(UserAccount.profile_uuid == uid).with_for_update())
                    if owner is None: continue
                    if await db.scalar(select(deliveries.c.id).where(deliveries.c.id == key)):
                        counts['duplicate'] += 1; continue
                    # Fresh state immediately before contact: edits/deletion/logout
                    # between enumeration and send cannot revive a stale snapshot.
                    fresh_devices = await active_devices(db, send_at, uid)
                    fresh = next((c for c in await candidates(db, uid, send_at)
                        if c['goal_id'] == item['goal_id'] and c['plan_id'] == item['plan_id']
                        and c['start_at'] == item['start_at'] and c['due_at'] == item['due_at']), None)
                    if not fresh or not any(d['id'] == device['id'] for d in fresh_devices): continue
                    if not await preference_allows(db, item, send_at): continue
                    suppressed = await feedback_received(db, item)
                    if not suppressed:
                        # Let the existing background extractor finish a new
                        # user turn; don't race its feedback/plan update.
                        pending = await db.scalar(select(ConversationMessage.id).where(
                            ConversationMessage.conversation_id == item['conversation_id'],
                            ConversationMessage.role == 'user',
                            ConversationMessage.position > item['confirmation_position'],
                            ConversationMessage.created_at > send_at - timedelta(minutes=2)).limit(1))
                        if pending: continue
                    try:
                        await db.execute(insert(deliveries), {'id': key, 'user_id': uid, 'device_id': device['id'],
                            'goal_id': item['goal_id'], 'plan_id': item['plan_id'],
                            'start_at': naive_utc(item['start_at']), 'due_at': naive_utc(item['due_at']),
                            'state': 'feedback' if suppressed else 'attempting', 'created_at': naive_utc(send_at), 'updated_at': naive_utc(send_at)})
                        await db.commit()
                    except IntegrityError:
                        await db.rollback(); counts['duplicate'] += 1; continue
                if suppressed: counts['suppressed'] += 1; continue
                payload = {'title': 'BA Coach · 活动后提醒',
                    'body': '如果方便，回来聊聊今天的活动吧。做了、没做或有变化，都可以。',
                    'subscription_id': device['id'], 'tag': key, 'url': '/?pa_reminder=1',
                    'expires_at': int((item['due_at'] + MAX_LATENESS).timestamp() * 1000)}
                try:
                    code = await asyncio.to_thread(sender, device, payload,
                        max(1, int((item['due_at'] + MAX_LATENESS - send_at).total_seconds())))
                except Exception:
                    code = None  # Never log endpoint tokens, keys, payloads or transport exception text.
                state = 'accepted' if code is not None and 200 <= code < 300 else 'unknown' if code is None else 'failed'
                async with sessionmaker() as db:
                    await db.execute(update(deliveries).where(deliveries.c.id == key).values(
                        state=state, http_status=code, updated_at=now()))
                    if code in {404, 410}:
                        await db.execute(delete(devices).where(devices.c.id == device['id']))
                    await db.commit()
                counts['accepted' if state == 'accepted' else 'failed'] += 1
    # Tokens have no reason to survive logout/expiry; delivery metadata has a
    # 30-day retention and contains no activity/chat/endpoint content.
    async with sessionmaker() as db:
        valid = select(AuthSession.id).where(AuthSession.expires_at > at)
        await db.execute(delete(devices).where(~devices.c.session_id.in_(valid)))
        await db.execute(delete(deliveries).where(deliveries.c.created_at < naive_utc(at - timedelta(days=30))))
        await db.execute(delete(deliveries).where(~deliveries.c.user_id.in_(select(UserAccount.profile_uuid))))
        await db.execute(update(deliveries).where(deliveries.c.state == 'attempting',
            deliveries.c.created_at < naive_utc(at - timedelta(minutes=5))).values(state='unknown', updated_at=now()))
        await db.commit()
    return counts
