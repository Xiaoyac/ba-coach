"""Scheduled chat messages, independent of browser permissions and LLM calls.

Each planned occurrence gets up to two: a nudge LEAD before the start and a
feedback/record reminder BUFFER after the end.

The receipt, message and transcript revision commit together. The normal chat
SSE/revision API delivers them to open clients; offline clients read them later.
Never mutate coaching progress or interleave a reminder with an unfinished turn.
"""
import hashlib
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, insert, select, update

from .chat_reminder_schema import reminders
from .config import get_settings
from .database_v2_schema import metadata as schema
from .models import Conversation, ConversationMessage, UserAccount
from .pa_push import candidates, feedback_received, preference_allows
from .pa_schedule import BUFFER, MAX_LATENESS, naive_utc, utc
from .v2_repository import now

log = logging.getLogger(__name__)

LEAD = timedelta(minutes=30)
KINDS = ('before', 'after')


def available():
    settings = get_settings()
    return settings.pa_chat_reminders_enabled and settings.database_schema_version == 'v2'


def reminder_text(item):
    end = (item['due_at'] - BUFFER).astimezone(ZoneInfo(item['timezone']))
    return (f'活动后提醒：按原计划，「{item["activity"]}」在{end:%m月%d日 %H:%M}结束，'
            '现在已过约一小时。如果方便，可以打开「每日记录」记下活动与心情；'
            '还没做或计划有变化，也可以告诉我。')


def before_text(item):
    start = item['start_at'].astimezone(ZoneInfo(item['timezone']))
    return (f'活动前提醒：按原计划，「{item["activity"]}」将在{start:%m月%d日 %H:%M}开始，'
            '大约还有半小时。准备好了就可以去做；如果今天不方便或想调整，也可以告诉我。')


def window(item, kind):
    """[due, until) for one reminder of a candidate occurrence."""
    if kind == 'before':
        # Never nudge once the activity should already have started.
        return item['start_at'] - LEAD, item['start_at']
    return item['due_at'], item['due_at'] + MAX_LATENESS


def receipt_id(item, kind):
    # The after-reminder keeps its original key, so receipts written before
    # pre-reminders existed still deduplicate.
    occurrence = f'{item["goal_id"]}|{item["start_at"].isoformat()}' + ('|before' if kind == 'before' else '')
    return hashlib.sha256(occurrence.encode()).hexdigest()


async def deliver(factory, item, at=None, kind='after'):
    """Recheck under the same profile/conversation locks used by plan/chat writes."""
    fixed_time = at is not None
    at = utc(at or now())
    due, until = window(item, kind)
    if not due <= at < until:
        return 'skipped'
    key = receipt_id(item, kind)
    async with factory() as db:
        async with db.begin():
            profiles = schema.tables['user_profile']
            # A no-op write is also a writer lock on SQLite; FOR UPDATE alone
            # would let overlapping test/development workers race allocations.
            owner = await db.execute(update(profiles).where(profiles.c.uuid == item['user_id'])
                                     .values(uuid=profiles.c.uuid))
            if not owner.rowcount:
                return 'skipped'
            conversation = (await db.execute(select(Conversation.id).where(
                Conversation.id == item['conversation_id'], Conversation.subject_id == item['user_id'])
                .with_for_update())).scalar_one_or_none()
            if conversation is None:
                return 'skipped'
            # Lock before the first consistent read so MySQL does not recheck
            # against a snapshot taken before a concurrent plan edit.
            for name, ident in (('pa_goals', item['goal_id']), ('module_two_record', item['plan_id']),
                                ('pa_cycles', item['cycle_id'])):
                table = schema.tables[name]
                await db.execute(select(table.c.id).where(table.c.id == ident).with_for_update())
            rt = schema.tables['conversation_runtime_states']
            state = (await db.execute(select(rt).where(rt.c.conversation_id == conversation)
                                     .with_for_update())).mappings().one_or_none()
            if not fixed_time:
                at = utc(now())
            if not due <= at < until:
                return 'skipped'
            # Do not insert into a paused session, a sandbox, a different goal,
            # an active review, or a pending confirmation exchange.
            if (not state or state['flow_status'] != 'waiting_execution'
                    or state['current_module'] not in {'module_3', 'module_4'}
                    or state['active_goal_id'] != item['goal_id']
                    or state['active_cycle_id'] != item['cycle_id']
                    or state['last_transition_reason'] == 'awaiting_record_confirmation'
                    or str((state['memory'] or {}).get('sandbox_mode')).lower() == 'true'):
                return 'skipped'
            if not await db.scalar(select(UserAccount.id).where(UserAccount.profile_uuid == item['user_id'])):
                return 'skipped'
            if await db.scalar(select(reminders.c.id).where(reminders.c.id == key)):
                return 'duplicate'
            fresh = next((c for c in await candidates(db, item['user_id'], at)
                          if all(c[k] == item[k] for k in ('goal_id', 'plan_id', 'cycle_id', 'start_at', 'due_at', 'conversation_id'))), None)
            if not fresh:
                return 'skipped'
            plans = schema.tables['module_two_record']
            confirmed_at = await db.scalar(select(ConversationMessage.created_at).where(
                ConversationMessage.id == select(plans.c.confirmation_message_id).where(
                    plans.c.id == item['plan_id']).scalar_subquery(),
                ConversationMessage.conversation_id == conversation,
                ConversationMessage.role == 'user'))
            # A plan confirmed inside the lead time is nudged by the chat itself.
            if not confirmed_at or (kind == 'before' and utc(confirmed_at) > due):
                return 'skipped'
            if not await preference_allows(db, fresh, at, delivery_table=reminders, delivered_states=('sent',)):
                return 'skipped'
            # Feedback only answers the after-reminder; nothing precedes a start.
            suppressed = kind == 'after' and await feedback_received(db, fresh)
            latest = (await db.execute(select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation).order_by(
                ConversationMessage.position.desc(), ConversationMessage.id.desc()).limit(1))).scalar_one_or_none()
            if not suppressed:
                # A recent turn may still be generating/extracting. Never let
                # this assistant row masquerade as its durable generated reply.
                recent = await db.scalar(select(ConversationMessage.id).where(
                    ConversationMessage.conversation_id == conversation, ConversationMessage.role == 'user',
                    ConversationMessage.created_at > at - timedelta(minutes=2)).limit(1))
                if (recent or not latest or latest.role == 'user'
                        or utc(latest.created_at) > at - timedelta(minutes=2)):
                    return 'deferred'
            await db.execute(insert(reminders), dict(id=key, user_id=item['user_id'],
                conversation_id=conversation, goal_id=item['goal_id'], plan_id=item['plan_id'],
                start_at=naive_utc(item['start_at']), due_at=naive_utc(due),
                state='feedback' if suppressed else 'sent', created_at=naive_utc(at)))
            if suppressed:
                return 'suppressed'
            # Reserve a standalone odd slot. start_turn continues allocating
            # even user slots and their following odd assistant reply slots.
            position = latest.position + (2 if latest.position % 2 else 3)
            message = ConversationMessage(conversation_id=conversation, position=position, role='assistant',
                content=reminder_text(fresh) if kind == 'after' else before_text(fresh), created_at=at, finish_reason='scheduled_reminder')
            db.add(message)
            await db.flush()
            await db.execute(update(reminders).where(reminders.c.id == key).values(message_id=message.id))
            # Recency is user-owned: do not make an old chat become the member's
            # /current conversation merely because a reminder was appended.
            await db.execute(update(Conversation).where(Conversation.id == conversation)
                             .values(revision=Conversation.revision + 1))
            return 'sent'


async def tick(factory, *, at=None):
    fixed_time = at is not None
    at = utc(at or now())
    counts = dict(sent=0, suppressed=0, duplicate=0, deferred=0, skipped=0, failed=0)
    goals = schema.tables['pa_goals']
    async with factory() as db:
        users = (await db.execute(select(goals.c.user_id).join(UserAccount,
            UserAccount.profile_uuid == goals.c.user_id).where(goals.c.status == 'active').distinct())).scalars().all()
    for uid in users:
        try:
            async with factory() as db:
                items = await candidates(db, uid, at)
            for item in items:
                for kind in KINDS:
                    if window(item, kind)[0] > at:
                        continue
                    result = await deliver(factory, item, at if fixed_time else None, kind)
                    counts[result] += 1
        except Exception as exc:
            # Isolate an account's failure; logs must not contain chat content.
            log.warning('chat_reminder_account_failed: %s', type(exc).__name__)
            counts['failed'] += 1
    async with factory() as db:
        await db.execute(delete(reminders).where(~reminders.c.conversation_id.in_(select(Conversation.id))))
        await db.execute(delete(reminders).where(~reminders.c.user_id.in_(select(UserAccount.profile_uuid))))
        await db.commit()
    return counts
