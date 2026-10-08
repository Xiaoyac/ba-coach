"""Read-only, user-reviewable daily activity suggestions from owned chat evidence.

These are drafts, not assessments. Unknown dates, times and ratings stay unknown.
No model call, business transition, or source/assessment write belongs here.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .database_v2_schema import metadata as schema
from .models import Conversation, ConversationMessage


class ActivityCandidate(BaseModel):
    key: str
    activity: str
    source_quote: str
    source_created_at: datetime
    occurred_on: date | None
    time_text: str | None
    time_slot: str
    note: str | None


class ActivityCandidates(BaseModel):
    items: list[ActivityCandidate]
    has_more: bool = False


_DAY = re.compile(r"(?<!\d)(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?!\d)|今天|昨天|前天|明天|后天")
_RELATIVE = {"今天": 0, "昨天": -1, "前天": -2, "明天": 1, "后天": 2}
_UNCERTAIN = re.compile(r"如果|假如|假设|要是|打算|准备|计划|想去|想要|本来|没(?:有)?(?:去|做|走|跑|打|骑|参加|进行|完成)|不(?:去|做|走|跑|打|骑)|未(?:做|走|跑|打|骑|完成)|撤回|记错|说错|取消|明天|后天")
_DAY_UNCERTAIN = re.compile(r"大前天|大后天|可能|大概|不确定|记不清|好像|或|每|之前|之后|前后")
_RANGE = re.compile(r"(?<![\d:：])(\d{1,2})[:：]00\s*[–—~～至到-]\s*(\d{1,2})[:：]00(?![:：\d])")


def evidence_day(quote: str, source_text: str, anchor: datetime, zone) -> date | None:
    """Only one unambiguous explicit date, anchored to the original user turn."""
    if _DAY_UNCERTAIN.search(source_text):
        return None
    matches = list(_DAY.finditer(quote))
    # Multiple temporal clauses need the user's confirmation, not a guessed link.
    if len(matches) != 1 or len(list(_DAY.finditer(source_text))) != 1:
        return None
    match = matches[0]
    try:
        if match.group(1):
            return date(*map(int, match.groups()))
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        return anchor.astimezone(zone).date() + timedelta(days=_RELATIVE[match.group()])
    except (ValueError, OverflowError):
        return None


def hour_range(text: str | None) -> str:
    if not text or re.search(r"约|左右|大概|差不多|早|晚|午|凌晨|半夜|跨|次日|隔天", text):
        return ""
    matches = list(_RANGE.finditer(text))
    if len(matches) != 1 or len(re.findall(r"\d{1,2}[:：]\d{2}", text)) != 2:
        return ""
    start, end = map(int, matches[0].groups())
    return f"{start:02}:00–{end:02}:00" if 0 <= start < end <= 23 else ""


async def read_activity_candidates(db, *, subject_id: str, session_id: str,
                                   on: date, zone) -> ActivityCandidates:
    # Select only the ID: do not load the relationship's full chat transcript.
    conversation_id = await db.scalar(select(Conversation.id).where(
        Conversation.session_id == session_id, Conversation.subject_id == subject_id))
    if conversation_id is None:
        raise HTTPException(404, "聊天不存在。")
    from .config import get_settings
    if get_settings().database_schema_version != "v2":
        return ActivityCandidates(items=[])
    events = schema.tables["pa_activity_events"]
    rows = (await db.execute(select(events, ConversationMessage.content.label("user_text"),
        ConversationMessage.created_at.label("source_created_at"))
        .join(ConversationMessage, (ConversationMessage.id == events.c.source_message_id)
              & (ConversationMessage.conversation_id == events.c.source_conversation_id))
        .where(events.c.user_id == subject_id, events.c.source_conversation_id == conversation_id,
               events.c.event_kind == "performed", events.c.status == "active",
               events.c.superseded_by_message_id.is_(None), ConversationMessage.role == "user")
        .order_by(ConversationMessage.created_at.desc(), events.c.source_message_id.desc(), events.c.event_index)
        .limit(201))).mappings().all()
    items, seen = [], set()
    for row in rows[:200]:
        quote, activity, body = row["source_quote"], row["activity_content"], row["user_text"]
        if not quote or not activity or quote not in body or activity not in quote:
            continue
        # Check the original wording as well: a quoted substring may omit "没有".
        # This is deliberately conservative and not a semantic classifier.
        negated_activity = re.search(r"(?:没(?:有)?|未|不曾|并未)(?!想到|觉得|感觉|感到)[^，。！？\n]{0,4}" + re.escape(activity), body)
        if _UNCERTAIN.search(body) or negated_activity:
            continue
        occurred_on = evidence_day(quote, body, row["source_created_at"], zone)
        if occurred_on is not None and occurred_on != on:
            continue
        time_text = row["occurred_at_text"]
        time_text = time_text if time_text and time_text in quote else None
        note = row["effect"]
        note = note if note and note in quote else None
        # Event IDs are regenerated on extraction. Source evidence is stable.
        key = hashlib.sha256(json.dumps([subject_id, conversation_id, row["source_message_id"],
            activity, quote], ensure_ascii=False).encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        items.append(ActivityCandidate(key=key, activity=activity, source_quote=quote,
            source_created_at=row["source_created_at"], occurred_on=occurred_on,
            time_text=time_text, time_slot=hour_range(time_text), note=note))
    return ActivityCandidates(items=items[:50], has_more=len(rows) > 200 or len(items) > 50)
