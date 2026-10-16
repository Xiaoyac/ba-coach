"""Validate an explicit resend; never silently replay unknown or completed turns."""
from fastapi import HTTPException
from sqlalchemy import select
from .models import Conversation, ConversationMessage


async def validate_retry(db, request, subject_id):
    if request.retry_of_message_id is None:
        return
    if not subject_id or not request.session_id or request.metadata or request.module:
        raise HTTPException(400, "无法重试这条消息，请重新核对对话记录。")
    conversation = await db.scalar(select(Conversation).where(
        Conversation.session_id == request.session_id, Conversation.subject_id == subject_id))
    if conversation is None:
        raise HTTPException(404, "对话不存在。")
    messages = (await db.scalars(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation.id).order_by(ConversationMessage.position))).all()
    users = [m for m in messages if m.role == "user"]
    if not users or users[-1].id != request.retry_of_message_id or users[-1].content != request.message:
        raise HTTPException(409, "对话已有新消息，请查看最新记录，不要重复重试旧问题。")
    target = users[-1]
    if any(m.role == "assistant" and m.position == target.position + 1 and m.content.strip()
           and not m.error_code for m in messages):
        raise HTTPException(409, "这条问题已经有完整回复，请查看最新记录。")
