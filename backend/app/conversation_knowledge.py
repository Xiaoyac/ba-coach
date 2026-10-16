"""Resolve retrieval architecture and mediation independently from owned settings.

The historical mediator_enabled boolean remains the architecture selector:
true = legacy, false = hybrid. It does not control the mediator node.
"""
from dataclasses import dataclass
from sqlalchemy import select
from .models import (AccountSettings, Conversation, ConversationKnowledgeSettings,
                     ConversationMediatorNodeSettings, UserAccount)


@dataclass(frozen=True)
class KnowledgePolicy:
    legacy: bool
    mediator_node_enabled: bool


async def knowledge_policy_for_conversation(db, *, session_id, subject_id) -> KnowledgePolicy:
    if not subject_id:
        return KnowledgePolicy(legacy=False, mediator_node_enabled=True)
    row = (await db.execute(select(AccountSettings.role,
            ConversationKnowledgeSettings.mediator_enabled,
            ConversationMediatorNodeSettings.enabled.label("node_enabled"))
        .select_from(Conversation)
        .join(UserAccount, UserAccount.profile_uuid == Conversation.subject_id)
        .outerjoin(AccountSettings, AccountSettings.account_id == UserAccount.id)
        .outerjoin(ConversationKnowledgeSettings,
                   ConversationKnowledgeSettings.conversation_id == Conversation.id)
        .outerjoin(ConversationMediatorNodeSettings,
                   ConversationMediatorNodeSettings.conversation_id == Conversation.id)
        .where(Conversation.session_id == session_id, Conversation.subject_id == subject_id))).one_or_none()
    # Unknown/unowned requests cannot borrow an administrator's preferences.
    # Members always use hybrid + mediation, including existing conversations.
    if row is None or row.role != "admin":
        return KnowledgePolicy(legacy=False, mediator_node_enabled=True)
    return KnowledgePolicy(legacy=row.mediator_enabled is not False,
                           mediator_node_enabled=row.node_enabled is True)


async def mediator_enabled_for_conversation(db, *, session_id, subject_id) -> bool:
    """Compatibility accessor: returns the legacy architecture selection only."""
    return (await knowledge_policy_for_conversation(db,
        session_id=session_id, subject_id=subject_id)).legacy
