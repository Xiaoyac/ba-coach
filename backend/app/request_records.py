"""Request details built on the exact owned-message attribution boundary."""
from collections import defaultdict

from .request_diagnostics import MessageRequests, requests_for_owned_messages


async def request_records(db, conversation, *, message_id: int | None = None) -> dict[int, MessageRequests]:
    """Keep the richer UI projection without a second event-linking algorithm."""
    assistants = [message for message in conversation.messages if message.role == "assistant"
                  and (message_id is None or message.id == message_id)]
    requests = await requests_for_owned_messages(
        db, owned_conversation=conversation, messages=assistants,
    )
    users_by_position = defaultdict(list)
    replies_by_position = defaultdict(list)
    for message in conversation.messages:
        if message.role == "user":
            users_by_position[message.position].append(message)
        elif message.role == "assistant":
            replies_by_position[message.position].append(message)
    result = {}
    for message in assistants:
        candidates = (users_by_position.get(message.position - 1, [])
                      if type(message.position) is int else [])
        user = (candidates[0] if len(candidates) == 1
                and len(replies_by_position[message.position]) == 1 else None)
        result[message.id] = MessageRequests(
            user_sent_at=user.created_at if user else None,
            assistant_created_at=message.created_at,
            requests=requests[message.id],
        )
    return result
