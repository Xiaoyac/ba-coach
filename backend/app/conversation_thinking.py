"""Owned, administrator-only preference for all conversation model calls."""
from sqlalchemy import select

from .models import AccountSettings, Conversation, ConversationReplySettings, UserAccount


async def effective_thinking(db, *, session_id: str, subject_id: str | None) -> bool | None:
    if not subject_id:
        return None
    row = (await db.execute(select(AccountSettings.role, ConversationReplySettings.thinking_enabled)
        .select_from(Conversation)
        .join(UserAccount, UserAccount.profile_uuid == Conversation.subject_id)
        .join(AccountSettings, AccountSettings.account_id == UserAccount.id)
        .outerjoin(ConversationReplySettings, ConversationReplySettings.conversation_id == Conversation.id)
        .where(Conversation.session_id == session_id, Conversation.subject_id == subject_id))).one_or_none()
    if row is None or row.role != "admin":
        return None
    return row.thinking_enabled if row.thinking_enabled is not None else True
