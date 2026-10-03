"""Execute an authenticated, version-bound goal-card button before generation."""
from __future__ import annotations

import json
from sqlalchemy import select

from .goal_card_interaction import confirmation_button_text, current_user_action
from .goal_card_workspace import read_card_row
from .models import Conversation, ConversationMessage
from .pa_card_tools import PACardTools, form_ui_enabled


async def confirm_goal_button(state, context):
    """None means ordinary chat; every returned result is a real tool outcome."""
    if (state.get('current_module') != 'module_2' or context.sessionmaker is None
            or not form_ui_enabled(context.settings, state)):
        return None
    async with context.sessionmaker() as db:
        conversation = (await db.execute(select(Conversation).where(
            Conversation.session_id == state['session_id'],
            Conversation.subject_id == state['subject_id']))).scalar_one_or_none()
        if conversation is None:
            return None
        action = await current_user_action(db, conversation, state['user_message_id'])
        if not action or action.get('action') != 'confirm':
            return None
        user = await db.get(ConversationMessage, state['user_message_id'])
        card = await read_card_row(db, conversation.id, state['subject_id'])
        if (not user or user.conversation_id != conversation.id or user.role != 'user'
                or not card or user.content != confirmation_button_text(card['kind'], action.get('revision'))):
            return None
    executor = PACardTools(maker=context.sessionmaker, session_id=state['session_id'],
        user_id=state['subject_id'], user_message_id=state['user_message_id'],
        module='module_2', ui_enabled=True)

    async def execute(name, args):
        return await executor.execute({'id': f'server_button_{name}', 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}})

    snapshot = await execute('get_pa_card', {})
    if snapshot.get('status') != 'ok':
        return snapshot
    name = 'confirm_pa_card' if card['kind'] == 'primary' else 'confirm_secondary_goal_card'
    args = {'state_version': snapshot['state_version']}
    if card['kind'] == 'secondary':
        args.update(card_id=action['card_id'], card_revision=action['revision'])
    # All ownership, boundary, version, display and clinical checks are repeated
    # by the existing executor inside its transaction; no proposal is a receipt.
    return await execute(name, args)
