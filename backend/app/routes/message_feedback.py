"""Owner-only reply ratings, with a separate administrator review projection."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..identity import CallerIdentity, require_admin, require_caller
from ..models import Conversation, ConversationMessage, MessageFeedback, _utcnow

router = APIRouter(tags=["message-feedback"])
Reason = Literal["off_topic", "repetitive", "incomplete", "incorrect", "uncomfortable", "other"]


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal["up", "down"] | None
    reasons: list[Reason] = Field(default_factory=list, max_length=6)
    comment: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def normalize(self):
        self.reasons = list(dict.fromkeys(self.reasons)) if self.rating == "down" else []
        self.comment = self.comment.strip() if self.rating == "down" else ""
        return self


class FeedbackItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    message_id: int
    rating: Literal["up", "down"]
    reasons: list[Reason]
    comment: str
    updated_at: datetime


@router.get("/message-feedback", response_model=list[FeedbackItem])
async def list_own_feedback(
    session_id: str = Query(min_length=1, max_length=128),
    caller: CallerIdentity = Depends(require_caller), db: AsyncSession = Depends(get_db),
):
    conversation = (await db.execute(select(Conversation.id).where(
        Conversation.session_id == session_id, Conversation.subject_id == caller.subject_id,
    ))).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(404, "找不到对话")
    return (await db.execute(select(MessageFeedback).join(
        ConversationMessage, ConversationMessage.id == MessageFeedback.message_id,
    ).where(ConversationMessage.conversation_id == conversation))).scalars().all()


@router.put("/message-feedback/{message_id}", response_model=FeedbackItem | None)
async def set_feedback(
    message_id: int, payload: FeedbackInput,
    caller: CallerIdentity = Depends(require_caller), db: AsyncSession = Depends(get_db),
):
    # Lock the parent even when the feedback row doesn't exist yet. Concurrent
    # writes from two tabs serialize around the same durable message identity.
    message = (await db.execute(select(ConversationMessage).join(Conversation).where(
        ConversationMessage.id == message_id, Conversation.subject_id == caller.subject_id,
    ).with_for_update())).scalar_one_or_none()
    if message is None:
        raise HTTPException(404, "找不到回复")
    if message.role != "assistant" or not message.content.strip():
        raise HTTPException(422, "只能评价有内容的 AI 回复")
    feedback = await db.get(MessageFeedback, message_id)
    if payload.rating is None:
        if feedback is not None:
            await db.delete(feedback)
        await db.commit()
        return None
    if feedback is None:
        feedback = MessageFeedback(message_id=message_id)
        db.add(feedback)
    feedback.rating = payload.rating
    feedback.reasons = payload.reasons
    feedback.comment = payload.comment
    feedback.updated_at = _utcnow()
    await db.commit()
    await db.refresh(feedback)
    return feedback


@router.get("/admin/message-feedback")
async def list_admin_feedback(
    rating: Literal["up", "down"] | None = None,
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=30, ge=1, le=100),
    _caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    statement = select(MessageFeedback, ConversationMessage, Conversation).join(
        ConversationMessage, ConversationMessage.id == MessageFeedback.message_id,
    ).join(Conversation, Conversation.id == ConversationMessage.conversation_id)
    if rating:
        statement = statement.where(MessageFeedback.rating == rating)
    if before:
        statement = statement.where(MessageFeedback.message_id < before)
    rows = (await db.execute(statement.order_by(MessageFeedback.message_id.desc()).limit(limit + 1))).all()
    # Explicit allowlist: no profile, reasoning, system prompt or debug payload.
    return {"items": [dict(
        FeedbackItem.model_validate(feedback).model_dump(mode="json"),
        session_id=conversation.session_id, title=conversation.title,
        content=message.content, model=message.model_name,
        request_id=message.provider_request_id,
    ) for feedback, message, conversation in rows[:limit]],
        "next_cursor": rows[limit - 1][0].message_id if len(rows) > limit else None}
