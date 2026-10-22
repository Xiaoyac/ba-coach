"""Owner-only reply ratings, with a separate administrator review projection."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..identity import CallerIdentity, require_admin, require_caller
from ..models import (
    Conversation, ConversationMessage, MessageFeedback, ResearchParticipant, UserAccount, _utcnow,
)
from ..models_business import UserProfile

router = APIRouter(tags=["message-feedback"])
DownReason = Literal["off_topic", "repetitive", "incomplete", "incorrect", "uncomfortable", "other"]
UpReason = Literal["understood", "supportive", "practical", "clear", "encouraging", "other"]
Reason = DownReason | UpReason
REASONS_BY_RATING = {"up": set(UpReason.__args__), "down": set(DownReason.__args__)}


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal["up", "down"] | None
    reasons: list[Reason] = Field(default_factory=list, max_length=6)
    comment: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def normalize(self):
        allowed = REASONS_BY_RATING.get(self.rating, set())
        if any(reason not in allowed for reason in self.reasons):
            raise ValueError("反馈原因与评价不匹配")
        self.reasons = list(dict.fromkeys(self.reasons))
        self.comment = self.comment.strip() if self.rating else ""
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
    # The rating is always the conversation owner's; name them for the reviewer.
    statement = select(
        MessageFeedback, ConversationMessage, Conversation,
        UserAccount.username, UserProfile.nickname, ResearchParticipant.research_id,
    ).join(
        ConversationMessage, ConversationMessage.id == MessageFeedback.message_id,
    ).join(Conversation, Conversation.id == ConversationMessage.conversation_id) \
        .outerjoin(UserAccount, UserAccount.profile_uuid == Conversation.subject_id) \
        .outerjoin(UserProfile, UserProfile.uuid == UserAccount.profile_uuid) \
        .outerjoin(ResearchParticipant, ResearchParticipant.account_id == UserAccount.id)
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
        username=username, display_name=nickname, research_id=research_id,
    ) for feedback, message, conversation, username, nickname, research_id in rows[:limit]],
        "next_cursor": rows[limit - 1][0].message_id if len(rows) > limit else None}
