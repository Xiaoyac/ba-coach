"""Resolve the temporary mediator bypass from current role and ownership."""
from sqlalchemy import select

from .models import AccountSettings, Conversation, ConversationKnowledgeSettings, UserAccount


async def mediator_enabled_for_conversation(db, *, session_id, subject_id) -> bool:
    if not subject_id:
        return True
    row = (await db.execute(select(AccountSettings.role, ConversationKnowledgeSettings.mediator_enabled)
        .select_from(Conversation)
        .join(UserAccount, UserAccount.profile_uuid == Conversation.subject_id)
        .join(AccountSettings, AccountSettings.account_id == UserAccount.id)
        .outerjoin(ConversationKnowledgeSettings,
                   ConversationKnowledgeSettings.conversation_id == Conversation.id)
        .where(Conversation.session_id == session_id, Conversation.subject_id == subject_id))).one_or_none()
    # Members and former admins keep the existing path. Missing settings mean
    # no experiment; changing a request body cannot enable the bypass.
    return not (row is not None and row.role == "admin" and row.mediator_enabled is False)
