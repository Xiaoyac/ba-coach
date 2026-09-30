"""Safe per-turn diagnostics. No prompts, credentials or private trace payloads."""
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import select

from .models import AIExecutionEvent, ConversationMessage


class RequestRecord(BaseModel):
    stage: str
    model: str | None = None
    request_id: str | None = None
    recorded_at: datetime | None = None
    duration_ms: int | None = None
    error_code: str | None = None


class MessageRequests(BaseModel):
    user_sent_at: datetime | None = None
    assistant_created_at: datetime | None = None
    requests: list[RequestRecord] = Field(default_factory=list)


async def request_records(db, conversation) -> dict[int, MessageRequests]:
    """Load once for a snapshot; correlate by durable IDs, never time proximity."""
    events = (await db.execute(select(AIExecutionEvent).where(
        AIExecutionEvent.conversation_id == conversation.id,
        AIExecutionEvent.subject_id == conversation.subject_id,
        AIExecutionEvent.session_id == conversation.session_id,
    ).order_by(AIExecutionEvent.id))).scalars().all()
    users = {m.position: m for m in conversation.messages if m.role == "user"}
    result = {}
    for message in conversation.messages:
        if message.role != "assistant":
            continue
        user = users.get(message.position - 1)
        linked = [e for e in events if e.assistant_message_id == message.id or (
            user is not None and (e.event_metadata or {}).get("user_message_id") == user.id)]
        entries = [RequestRecord(stage=e.stage, model=e.model_name,
            request_id=e.provider_request_id, recorded_at=e.created_at,
            duration_ms=e.duration_ms, error_code=e.error_code) for e in linked
            if e.model_name or e.provider_request_id]
        if not any(e.stage == "main_generation" for e in entries) and message.model_name:
            entries.insert(0, RequestRecord(stage="main_generation", model=message.model_name,
                request_id=message.provider_request_id, recorded_at=message.created_at,
                duration_ms=message.main_generation_duration_ms))
        result[message.id] = MessageRequests(user_sent_at=user.created_at if user else None,
            assistant_created_at=message.created_at, requests=entries)
    return result
