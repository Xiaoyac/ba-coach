"""Read-only, owner-scoped history budget estimates for the composer."""
from sqlalchemy import select
from .context_epochs import estimate_tokens, source_digest
from .models import ConversationMessage, ConversationContextCheckpoint


async def history_usage(db, conversation_id, *, settings, epoch_enabled):
    rows = list((await db.execute(select(ConversationMessage.id, ConversationMessage.role,
        ConversationMessage.content, ConversationMessage.created_at).where(
        ConversationMessage.conversation_id == conversation_id,
        ConversationMessage.role.in_(['user', 'assistant'])).order_by(
        ConversationMessage.position, ConversationMessage.id))).all())
    total = len(rows)
    summarized = 0
    summary = ''
    if epoch_enabled:
        checkpoint = await db.get(ConversationContextCheckpoint, conversation_id)
        if checkpoint and checkpoint.summary:
            index = next((i for i, row in enumerate(rows) if row.id == checkpoint.through_message_id), None)
            if index is not None and source_digest(rows[:index + 1]) == checkpoint.source_digest:
                summarized = index + 1
                summary = checkpoint.summary
                rows = rows[summarized:]
    else:
        # Match the legacy newest-message window, starting at a user boundary.
        limit = settings.max_history_messages
        if len(rows) > limit:
            rows = rows[-limit:] if limit else []
            while rows and rows[0].role != 'user':
                rows = rows[1:]
    return dict(estimated_tokens=estimate_tokens(summary) + sum(estimate_tokens(r.content) for r in rows),
        token_budget=settings.main_history_token_budget if epoch_enabled else None,
        retained_messages=len(rows), summarized_messages=summarized, total_messages=total,
        message_budget=None if epoch_enabled else settings.max_history_messages,
        mode='compression' if epoch_enabled else 'window')
