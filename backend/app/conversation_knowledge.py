"""Resolve mutually exclusive legacy/hybrid modes from current role/ownership.

The existing durable boolean is retained for API/database compatibility:
true = legacy retrieval plus mediator; false = hybrid retrieval plus bounded
non-thinking mediation. The historic field name is a mode selector only.
"""
from sqlalchemy import select

from .models import AccountSettings, Conversation, ConversationKnowledgeSettings, UserAccount


async def mediator_enabled_for_conversation(db, *, session_id, subject_id) -> bool:
    if not subject_id:
        return True
    row = (await db.execute(select(AccountSettings.role, ConversationKnowledgeSettings.mediator_enabled)
        .select_from(Conversation)
        .join(UserAccount, UserAccount.profile_uuid == Conversation.subject_id)
        .outerjoin(AccountSettings, AccountSettings.account_id == UserAccount.id)
        .outerjoin(ConversationKnowledgeSettings,
                   ConversationKnowledgeSettings.conversation_id == Conversation.id)
        .where(Conversation.session_id == session_id, Conversation.subject_id == subject_id))).one_or_none()
    if row is None:
        return True  # Unknown or unowned conversations cannot select a mode.
    # Members use hybrid retrieval on every turn, including existing chats.
    # Missing account settings mean a regular user, as in the auth projection.
    # Only admins retain the explicit legacy/hybrid comparison preference.
    if row.role != "admin":
        return False
    return row.mediator_enabled is not False
