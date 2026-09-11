"""Delete a transcript without deleting user-owned goals or cycle history."""
from sqlalchemy import select, update, delete
from .database_v2_schema import metadata as schema
from .models import Conversation, ConversationMessage, AIExecutionEvent, ConversationModuleProgress


async def detach_conversation(db, conversation):
    ids = select(ConversationMessage.id).where(ConversationMessage.conversation_id == conversation.id)
    # Evidence IDs are retained as historical IDs, but audit entries explicitly
    # state that their source transcript is no longer accessible.
    await db.execute(update(AIExecutionEvent).where(AIExecutionEvent.conversation_id == conversation.id)
                     .values(conversation_id=None, assistant_message_id=None))
    for name, column in (("pa_goals", "created_from_conversation_id"),
                         ("pa_cycles", "started_from_conversation_id"), ("ai_decision_logs", "conversation_id")):
        table = schema.tables[name]
        await db.execute(update(table).where(table.c[column] == conversation.id).values(**{column: None}))
    rt = schema.tables["conversation_runtime_states"]
    await db.execute(delete(rt).where(rt.c.conversation_id == conversation.id))
    # The obsolete sidecar may still be retained for audit, but its old FK
    # cannot prevent the user's explicit transcript deletion.
    await db.execute(delete(ConversationModuleProgress).where(ConversationModuleProgress.conversation_id == conversation.id))
    await db.execute(delete(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id))
    await db.execute(delete(Conversation).where(Conversation.id == conversation.id))
