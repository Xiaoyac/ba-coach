"""Read-only, turn-scoped request IDs; never publish raw execution metadata."""
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AIExecutionEvent, Conversation, ConversationMessage


def _utc(value: datetime | None) -> datetime | None:
    # Application timestamp columns are UTC even when a SQL driver returns a
    # naive datetime. Keep that zone in JSON so browsers cannot reinterpret it.
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class ModelRequestInfo(BaseModel):
    stage: str
    request_id: str | None = None
    provider: str | None = None
    model: str | None = None
    recorded_at: datetime | None = None
    duration_ms: int | None = None
    error_code: str | None = None

    _normalize_recorded_at = field_validator("recorded_at")(_utc)


class MessageRequests(BaseModel):
    user_sent_at: datetime | None = None
    assistant_created_at: datetime | None = None
    requests: list[ModelRequestInfo] = Field(default_factory=list)

    _normalize_times = field_validator("user_sent_at", "assistant_created_at")(_utc)


_MODEL_STAGES = frozenset({
    "main_generation", "module_router", "knowledge_mediator", "risk_gate",
    "clinical_extraction", "module_summarizer", "memory_summary",
})


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


async def requests_for_owned_messages(
    db: AsyncSession, *, owned_conversation: Conversation,
    messages: Sequence[ConversationMessage],
) -> dict[int, list[ModelRequestInfo]]:
    """Project requests for already-authorized assistant rows in one conversation.

    Callers must resolve the conversation against the authenticated subject
    first. Events must still match its account, session and conversation. An
    explicit assistant binding wins; otherwise only a unique reserved user
    slot can bind a pre-reply event. Timestamps are never used as attribution.
    Two batched reads serve the whole share, irrespective of message count.
    """
    conversation = owned_conversation
    if any(message.role != "assistant" or message.conversation_id != conversation.id
           for message in messages):
        raise ValueError("requests_require_owned_assistant_messages")
    by_id = {message.id: message for message in messages}
    result: dict[int, list[ModelRequestInfo]] = {message_id: [] for message_id in by_id}
    if not by_id:
        return result

    # Old imports can contain duplicate positions: ambiguous slots must never
    # make one request appear under more than one reply.
    rows = (await db.execute(select(ConversationMessage.id, ConversationMessage.position,
                                    ConversationMessage.role).where(
        ConversationMessage.conversation_id == conversation.id,
    ))).all()
    users_by_position = defaultdict(list)
    replies_by_position = defaultdict(list)
    for row_id, position, role in rows:
        if role == "user":
            users_by_position[position].append(row_id)
        elif role == "assistant":
            replies_by_position[position].append(row_id)
    user_for_reply = {}
    replies_for_user = defaultdict(list)
    for message in messages:
        candidates = users_by_position.get(message.position - 1, []) if type(message.position) is int else []
        if len(candidates) == 1 and len(replies_by_position[message.position]) == 1:
            user_for_reply[message.id] = candidates[0]
            replies_for_user[candidates[0]].append(message.id)
    reply_for_user = {user_id: ids[0] for user_id, ids in replies_for_user.items() if len(ids) == 1}
    scopes = [AIExecutionEvent.assistant_message_id.in_(by_id)]
    if reply_for_user:
        scopes.append(and_(AIExecutionEvent.assistant_message_id.is_(None),
            AIExecutionEvent.event_metadata["user_message_id"].as_integer().in_(reply_for_user)))
    events = (await db.execute(select(AIExecutionEvent).where(
        AIExecutionEvent.conversation_id == conversation.id,
        AIExecutionEvent.subject_id == conversation.subject_id,
        AIExecutionEvent.session_id == conversation.session_id,
        AIExecutionEvent.stage.in_(_MODEL_STAGES), or_(*scopes),
    ).order_by(AIExecutionEvent.id))).scalars().all()

    def append(message_id, stage, request_id, provider, model, *, event=None,
               recorded_at=None, duration_ms=None, error_code=None):
        item = ModelRequestInfo(stage=stage, request_id=_text(request_id),
                                provider=_text(provider), model=_text(model),
                                recorded_at=event.created_at if event is not None else recorded_at,
                                duration_ms=event.duration_ms if event is not None else duration_ms,
                                error_code=_text(event.error_code) if event is not None else _text(error_code))
        if item not in result[message_id]:
            result[message_id].append(item)

    def recovery_details(event, recovery):
        duration = recovery.get("duration_ms")
        return {"recorded_at": event.created_at,
                "duration_ms": duration if type(duration) is int and duration >= 0 else None,
                "error_code": recovery.get("error_code")}

    for event in events:
        metadata = event.event_metadata if isinstance(event.event_metadata, dict) else {}
        user_id = metadata.get("user_message_id")
        if event.assistant_message_id is not None:
            message_id = event.assistant_message_id
            if message_id not in by_id:
                continue
            if user_id is not None and (type(user_id) is not int or user_for_reply.get(message_id) != user_id):
                continue  # Conflicting explicit bindings cannot be repaired by guessing.
        else:
            message_id = reply_for_user.get(user_id) if type(user_id) is int else None
            if message_id is None:
                continue
        request_id = event.provider_request_id
        model = event.model_name
        if event.stage == "main_generation":
            request_id = _text(request_id) or by_id[message_id].provider_request_id
            model = _text(model) or by_id[message_id].model_name
        native = metadata.get('pa_tools')
        if event.stage=='main_generation' and isinstance(native,dict) and native.get('requests'):
            for index,request in enumerate(native['requests']):
                append(message_id, 'main_generation' if index==0 else 'pa_tool_continuation',
                       request.get('request_id'),event.provider,model,recorded_at=event.created_at,
                       duration_ms=request.get('duration_ms'),error_code=request.get('error_code'))
            continue
        recovery = metadata.get("json_recovery")
        reply_recovery = metadata.get("reply_recovery")
        if (event.stage == "main_generation" and isinstance(reply_recovery, dict)
                and ("original_request_id" in reply_recovery or "request_id" in reply_recovery)):
            retry_id = _text(reply_recovery.get("request_id"))
            original_id = _text(reply_recovery.get("original_request_id"))
            # Successful recovery may replace the main event/row's ID. Do
            # not relabel that retry as the original upstream generation.
            if original_id is None and _text(request_id) != retry_id:
                original_id = request_id
            append(message_id, "main_generation", original_id, event.provider, model,
                   recorded_at=event.created_at)
            if "request_id" in reply_recovery:
                append(message_id, "main_generation_recovery", retry_id, event.provider, model,
                       **recovery_details(event, reply_recovery))
        elif event.stage == "module_router" and isinstance(recovery, dict) and recovery:
            append(message_id, "module_router_original",
                   _text(recovery.get("original_request_id")) or request_id, event.provider, model,
                   recorded_at=event.created_at)
            if "request_id" in recovery:
                append(message_id, "module_router_recovery", recovery.get("request_id"), event.provider, model,
                       **recovery_details(event, recovery))
        else:
            append(message_id, event.stage, request_id, event.provider, model, event=event)

    for message_id, items in result.items():
        if not any(item.stage == "main_generation" for item in items):
            # The durable reply column predates these diagnostic events. A
            # missing value stays null rather than inventing an upstream ID.
            message = by_id[message_id]
            items.insert(0, ModelRequestInfo(stage="main_generation",
                request_id=_text(message.provider_request_id), model=_text(message.model_name),
                recorded_at=message.created_at, duration_ms=message.main_generation_duration_ms))
    return result
