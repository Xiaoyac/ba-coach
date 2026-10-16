"""Client-reported display timing; separate from server/model latency."""
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from fastapi import HTTPException
from .models import AIExecutionEvent, Conversation, ConversationMessage

STAGE = "client_response_display"


class ClientTiming(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    user_message_id: int = Field(gt=0)
    first_body_ms: int = Field(ge=0, le=86_400_000)


async def record_client_timing(db, conversation, payload):
    # Serialise submissions for this conversation. A retry cannot overwrite the
    # first measurement or create duplicate events in the production database.
    await db.scalar(select(Conversation.id).where(Conversation.id == conversation.id).with_for_update())
    user = await db.scalar(select(ConversationMessage.id).where(
        ConversationMessage.id == payload.user_message_id,
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.role == "user"))
    if user is None:
        raise HTTPException(404, "Turn not found")
    existing = await db.scalar(select(AIExecutionEvent).where(
        AIExecutionEvent.conversation_id == conversation.id,
        AIExecutionEvent.subject_id == conversation.subject_id,
        AIExecutionEvent.stage == STAGE,
        AIExecutionEvent.event_metadata["user_message_id"].as_integer() == user).limit(1))
    if existing is None:
        existing = AIExecutionEvent(conversation_id=conversation.id,
            subject_id=conversation.subject_id, session_id=conversation.session_id,
            stage=STAGE, duration_ms=payload.first_body_ms,
            event_metadata={"user_message_id": user, "measurement": "client_click_to_body_paint"})
        db.add(existing)
    await db.commit()
    return {"first_body_ms": existing.duration_ms}
