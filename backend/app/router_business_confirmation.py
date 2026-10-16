"""Accept an already shown version without giving code authority over routing."""
import logging
from sqlalchemy import update
from .database_v2_schema import metadata
from .dialogue_confirmation import precommit_user_confirmation
from .routing_modes import effective_routing_mode, ROUTER_ONLY
from .v2_workflow import runtime_for

logger = logging.getLogger(__name__)


async def settle_confirmation(state, context):
    receipt = None
    try:
        async with context.sessionmaker() as db:
            conversation, actual = await runtime_for(db, state['session_id'])
            expected = state.get('routing_state') or {}
            if (not conversation or not actual
                    or await effective_routing_mode(db, conversation=conversation, state=actual,
                        user_id=state['subject_id'], lock=True) != ROUTER_ONLY
                    or any(actual[key] != expected.get(key) for key in
                        ('current_module', 'row_version', 'active_goal_id', 'active_cycle_id'))):
                return None
            source_module = actual['current_module']
            receipt = await precommit_user_confirmation(db, session_id=state['session_id'],
                user_id=state['subject_id'], user_message_id=state['user_message_id'],
                confirmation_provider=context.router_provider)
            if not receipt:
                return None
            # The confirmation service updates business state and normal peers.
            # Its conventional next module does not select this chat's reply.
            runtime = metadata.tables['conversation_runtime_states']
            await db.execute(update(runtime).where(runtime.c.conversation_id == conversation.id)
                .values(current_module=source_module))
            await db.commit()
        return {'module': receipt[0], 'cycle_id': receipt[1], 'source': 'verified_router_only_card'}
    except Exception:
        logger.exception('Router-only card confirmation failed; no success receipt published')
        return None
