"""Owned member chats use parallel low-effort replies; admins retain choices."""
from sqlalchemy import select

from .models import (AccountSettings, Conversation, ConversationResponseMode,
                     ConversationReplyEffort, UserAccount)

STANDARD = "standard"
ACK_DEEP = "ack_deep"


async def effective_reply_mode(db, *, session_id: str, subject_id: str | None) -> str:
    """Resolve server policy from current ownership and the persisted role."""
    if not subject_id:
        return STANDARD
    row = (await db.execute(select(AccountSettings.role, ConversationResponseMode.mode)
        .select_from(Conversation)
        .join(UserAccount, UserAccount.profile_uuid == Conversation.subject_id)
        .join(AccountSettings, AccountSettings.account_id == UserAccount.id)
        .outerjoin(ConversationResponseMode, ConversationResponseMode.conversation_id == Conversation.id)
        .where(Conversation.session_id == session_id, Conversation.subject_id == subject_id))).one_or_none()
    if row is not None and row.role == "user":
        return ACK_DEEP
    if row is None or row.role != "admin" or row.mode != ACK_DEEP:
        return STANDARD
    return ACK_DEEP


async def effective_reply_effort(db, *, session_id: str, subject_id: str | None) -> str | None:
    """Read a server-owned preference only while the response experiment is authorized."""
    if await effective_reply_mode(db, session_id=session_id, subject_id=subject_id) != ACK_DEEP:
        return None
    role = await db.scalar(select(AccountSettings.role).join(
        UserAccount, UserAccount.id == AccountSettings.account_id).where(UserAccount.profile_uuid == subject_id))
    if role == "user":
        # Existing preferences (including former admin choices) cannot raise
        # the member compute budget above the product's low default.
        return "low"
    saved = await db.scalar(select(ConversationReplyEffort.effort)
        .join(Conversation, Conversation.id == ConversationReplyEffort.conversation_id)
        .where(Conversation.session_id == session_id, Conversation.subject_id == subject_id))
    # Old B conversations keep the deployed low default without a data rewrite.
    return saved if saved in {"low", "high", "max"} else "low"


async def configured_reply_effort_options(db, *, subject_id: str) -> tuple[str, ...]:
    """Advertise the owner's preferred/default model; each turn checks its real provider again."""
    from .config import get_settings
    from .generation_policy import reply_effort_options
    row = (await db.execute(select(AccountSettings.role, AccountSettings.preferred_provider)
        .join(UserAccount, UserAccount.id == AccountSettings.account_id)
        .where(UserAccount.profile_uuid == subject_id))).one_or_none()
    if row is None or row.role != "admin":
        return ()
    settings = get_settings()
    provider = row.preferred_provider or settings.default_provider
    return reply_effort_options(settings, provider,
        model=str(getattr(settings, f"{provider}_model", "") or ""))
