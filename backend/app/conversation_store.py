"""Writes each finished chat turn into the durable conversation transcript.

Called from `routes/chat.py` right after a turn completes — not from inside
the LangGraph nodes. `SessionStore` (session.py) already decides, inside
`update_memory_and_format_node`, exactly which two messages a turn contributes
(the user's input, and the reply only if it is non-empty — see that node for
why). This module makes the same call at the route layer rather than
importing graph internals, and appends them to `conversations` /
`conversation_messages`: durable, subject-scoped, never trimmed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .ai_telemetry import add_ai_event
from .models import Conversation, ConversationMessage, ConversationRuntimeState
from .opening import OPENING_MESSAGE_TEXT
from .reasoning import normalize_reasoning_channels
from .workflow_state import record_interaction_turn

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 28
SANDBOX_TITLE_PREFIX = "沙盒 · "


async def create_conversation_with_opening(
    db: AsyncSession, *, subject_id: str, session_id: str
) -> Conversation:
    """Create a durable conversation whose first turn is the BA opening.

    Position ``-1`` deliberately precedes the existing user/reply pairs
    (0/1, 2/3, ...), so no existing allocator or transcript ordering changes.
    """
    conversation = Conversation(
        subject_id=subject_id,
        session_id=session_id,
        title="新对话",
        revision=1,
    )
    db.add(conversation)
    await db.flush()
    from .v2_profile import enabled as v2_enabled
    initial = None
    opening_text = OPENING_MESSAGE_TEXT
    if v2_enabled():
        from .v2_repository import initial_module
        initial = await initial_module(db, user_id=subject_id)
        if initial == "module_2":
            opening_text = "欢迎回来。之前的问题理解进度会保留；你可以继续一个已有目标，或直接告诉我想讨论的新方向，我们一起把它具体化。"
    db.add(
        ConversationMessage(
            conversation_id=conversation.id,
            position=-1,
            role="assistant",
            content=opening_text,
        )
    )
    db.add(
        ConversationRuntimeState(
            conversation_id=conversation.id,
            module=initial,
            memory={},
        )
    )
    await db.commit()
    await db.refresh(conversation, attribute_names=["messages"])
    return conversation


async def create_sandbox_conversation(
    db: AsyncSession, *, subject_id: str, session_id: str, module: str
) -> Conversation:
    """Create an empty admin sandbox at the beginning of ``module``.

    A sandbox deliberately has no opening message, prior transcript, module
    memory, or PA card.  Selecting a module twice therefore never resumes the
    first attempt: each selection gets a new session and a clean module start.
    The memory marker survives backend restarts and lets graph post-processing
    avoid writing test turns into the administrator's clinical programme data.
    """
    roman = {"module_1": "I", "module_2": "II", "module_3": "III", "module_4": "IV"}
    conversation = Conversation(
        subject_id=subject_id,
        session_id=session_id,
        title=f"{SANDBOX_TITLE_PREFIX}MODULE {roman[module]}",
        revision=0,
    )
    db.add(conversation)
    await db.flush()
    db.add(
        ConversationRuntimeState(
            conversation_id=conversation.id,
            module=module,
            memory={
                "sandbox_mode": "true",
                "sandbox_start_module": module,
            },
        )
    )
    await db.commit()
    await db.refresh(conversation, attribute_names=["messages"])
    return conversation


async def backfill_opening_messages(db: AsyncSession) -> int:
    """Add the canonical opening to transcripts created by older versions.

    Idempotent by content and role.  This is a data migration rather than a
    synthetic response-layer prepend: once repaired, every API consumer and
    the model itself reads exactly the same transcript.
    """
    has_opening = exists(
        select(ConversationMessage.id).where(
            ConversationMessage.conversation_id == Conversation.id,
            ConversationMessage.role == "assistant",
            ConversationMessage.content == OPENING_MESSAGE_TEXT,
        )
    )
    ids = (
        await db.execute(
            select(Conversation.id).where(
                ~has_opening,
                ~Conversation.title.startswith(SANDBOX_TITLE_PREFIX),
            )
        )
    ).scalars().all()
    for conversation_id in ids:
        db.add(
            ConversationMessage(
                conversation_id=conversation_id,
                position=-1,
                role="assistant",
                content=OPENING_MESSAGE_TEXT,
            )
        )
        conversation = await db.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.revision += 1
    if ids:
        await db.commit()
    return len(ids)


async def save_runtime_state(
    db: AsyncSession,
    *,
    session_id: str,
    module: str | None,
    memory: dict[str, str],
) -> None:
    """Persist the module pointer and compact memory after a completed turn."""
    conversation = (
        await db.execute(
            select(Conversation).where(Conversation.session_id == session_id)
        )
    ).scalar_one_or_none()
    if conversation is None:
        return
    state = await db.get(ConversationRuntimeState, conversation.id)
    if state is None:
        state = ConversationRuntimeState(conversation_id=conversation.id)
        db.add(state)
    state.module = module
    state.memory = dict(memory)
    await db.commit()


def _derive_title(text: str) -> str:
    collapsed = " ".join(text.split())
    if not collapsed:
        return "新对话"
    if len(collapsed) <= TITLE_MAX_CHARS:
        return collapsed
    return collapsed[: TITLE_MAX_CHARS - 1] + "…"


async def start_turn(
    db: AsyncSession,
    *,
    subject_id: str,
    session_id: str,
    user_text: str,
) -> int:
    """Persist the user's message immediately and return its row id.

    The conversation row is locked while the next position is allocated.
    The unique-id retry handles the first-message race where two devices both
    initially observe that the conversation row does not yet exist.

    Note on trust: `session_id` here is whatever the LangGraph session store
    already accepted, and that store has no subject scoping of its own — two
    different subject ids can already continue the same session today if one
    learns the other's session id. This does not make that any worse; it is
    an existing property of the anonymous session model, not something this
    function is responsible for closing.
    """
    result = await db.execute(
        select(Conversation)
        .where(Conversation.session_id == session_id)
        .with_for_update()
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        conversation = Conversation(
            subject_id=subject_id,
            session_id=session_id,
            title=_derive_title(user_text),
        )
        db.add(conversation)
        try:
            await db.flush()  # assigns conversation.id, needed for the FK below
        except IntegrityError:
            # A concurrent worker inserted this same session first. This
            # transaction had no other writes, so retry cleanly against the
            # winner instead of dropping the user's message.
            await db.rollback()
            conversation = (
                await db.execute(
                    select(Conversation)
                    .where(Conversation.session_id == session_id)
                    .with_for_update()
                )
            ).scalar_one()

    max_position = (
        await db.execute(
            select(func.max(ConversationMessage.position)).where(
                ConversationMessage.conversation_id == conversation.id
            )
        )
    ).scalar_one()
    # User rows are even and reserve the following odd slot for their reply.
    # A second device may add its user row while the first model is thinking;
    # skip the pending reply slot and allocate the next pair.
    if max_position is None:
        position = 0
    else:
        position = max_position + (1 if max_position % 2 else 2)

    message = ConversationMessage(
        conversation_id=conversation.id,
        position=position,
        role="user",
        content=user_text,
    )
    db.add(message)

    conversation.updated_at = datetime.now(timezone.utc)
    conversation.revision += 1
    await db.commit()
    return message.id


async def finish_turn(
    db: AsyncSession,
    *,
    session_id: str,
    user_message_id: int,
    reply_text: str,
    reasoning_content: str = "",
    model_name: str | None = None,
    provider_name: str | None = None,
    routing_reasoning_content: str = "",
    router_model_name: str | None = None,
    telemetry: dict | None = None,
) -> int | None:
    """Append the assistant reply after generation, if one exists."""
    normalized = normalize_reasoning_channels(reply_text, reasoning_content)
    reply_text = normalized.reply
    reasoning_content = normalized.reasoning
    if not reply_text:
        return None

    row = (
        await db.execute(
            select(ConversationMessage, Conversation)
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .where(
                ConversationMessage.id == user_message_id,
                ConversationMessage.role == "user",
                Conversation.session_id == session_id,
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        return None
    user_message, conversation = row
    metrics = telemetry or {}
    usage = metrics.get("usage") or {}
    message = ConversationMessage(
        conversation_id=conversation.id,
        position=user_message.position + 1,
        role="assistant",
        content=reply_text,
        reasoning_content=reasoning_content or None,
        model_name=model_name,
        routing_reasoning_content=routing_reasoning_content or None,
        router_model_name=router_model_name,
        risk_gate_duration_ms=metrics.get("risk_gate_duration_ms"),
        time_to_first_reasoning_token_ms=metrics.get(
            "time_to_first_reasoning_token_ms"
        ),
        time_to_first_content_token_ms=metrics.get("time_to_first_content_token_ms"),
        main_generation_duration_ms=metrics.get("main_generation_duration_ms"),
        router_duration_ms=metrics.get("router_duration_ms"),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        reasoning_tokens=usage.get("reasoning_tokens"),
        provider_request_id=metrics.get("provider_request_id"),
        finish_reason=metrics.get("finish_reason"),
        error_code=metrics.get("error_code"),
        prompt_version=metrics.get("prompt_version"),
    )
    db.add(message)
    await db.flush()
    runtime = await db.get(ConversationRuntimeState, conversation.id)
    await record_interaction_turn(
        db, conversation=conversation, module=runtime.module if runtime else None
    )
    await add_ai_event(
        db,
        stage="main_generation",
        session_id=session_id,
        subject_id=conversation.subject_id,
        provider=provider_name,
        model_name=model_name,
        duration_ms=metrics.get("main_generation_duration_ms"),
        usage=usage,
        assistant_message_id=message.id,
        request_id=metrics.get("provider_request_id"),
        finish_reason=metrics.get("finish_reason"),
        error_code=metrics.get("error_code"),
        prompt_version=metrics.get("prompt_version"),
        event_metadata={
            "risk_gate_duration_ms": metrics.get("risk_gate_duration_ms"),
            "time_to_first_reasoning_token_ms": metrics.get("time_to_first_reasoning_token_ms"),
            "time_to_first_content_token_ms": metrics.get("time_to_first_content_token_ms"),
            "reply_recovery": metrics.get("reply_recovery"),
            **{key: metrics.get(key) for key in ("main_input", "reply_trace", "prompt_sources",
                "execution_timeline", "time_to_first_visible_content_ms", "first_visible_measurement",
                "router_pre_reply")},
            # Permission-controlled execution trace; never include this in
            # ordinary user-visible chat content.
            "answer_validator": metrics.get("answer_validator"),
            "knowledge_references": ({
                **metrics["knowledge_references"],
                "validator_status": (metrics.get("answer_validator") or {}).get("status"),
            } if metrics.get("knowledge_references") else None),
        },
    )
    conversation.updated_at = datetime.now(timezone.utc)
    conversation.revision += 1
    await db.commit()
    return message.id


async def complete_background_routing(
    db: AsyncSession,
    *,
    subject_id: str,
    session_id: str,
    assistant_message_id: int,
    module: str,
    memory: dict[str, str],
    routing_reasoning_content: str,
    router_model_name: str,
    router_duration_ms: int,
    router_provider: str | None = None,
    usage: dict[str, int] | None = None,
    request_id: str | None = None,
    finish_reason: str | None = None,
    error_code: str | None = None,
    prompt_version: str | None = None,
    workflow_decision: dict | None = None,
) -> bool:
    """Atomically publish a delayed router result and durable graph state."""
    row = (
        await db.execute(
            select(ConversationMessage, Conversation)
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .where(
                ConversationMessage.id == assistant_message_id,
                ConversationMessage.role == "assistant",
                Conversation.session_id == session_id,
                Conversation.subject_id == subject_id,
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        return False

    message, conversation = row
    message.routing_reasoning_content = routing_reasoning_content or None
    message.router_model_name = router_model_name or None
    message.router_duration_ms = router_duration_ms

    runtime = await db.get(ConversationRuntimeState, conversation.id)
    if runtime is None:
        runtime = ConversationRuntimeState(conversation_id=conversation.id)
        db.add(runtime)
    runtime.module = module
    runtime.memory = dict(memory)

    await add_ai_event(
        db,
        stage="module_router",
        session_id=session_id,
        subject_id=subject_id,
        provider=router_provider,
        model_name=router_model_name,
        duration_ms=router_duration_ms,
        usage=usage,
        assistant_message_id=assistant_message_id,
        request_id=request_id,
        finish_reason=finish_reason,
        error_code=error_code,
        prompt_version=prompt_version,
        event_metadata={"next_module": module, "workflow_decision": workflow_decision},
    )

    # Router enrichment is not new user activity. Revision still changes so
    # cross-device polling sees it, but sidebar recency must remain stable.
    conversation.revision += 1
    await db.commit()
    return True


async def record_turn(
    db: AsyncSession,
    *,
    subject_id: str,
    session_id: str,
    user_text: str,
    reply_text: str,
    reasoning_content: str = "",
    model_name: str | None = None,
    provider_name: str | None = None,
    routing_reasoning_content: str = "",
    router_model_name: str | None = None,
    telemetry: dict | None = None,
) -> None:
    """Compatibility wrapper for callers that already hold a full turn."""
    user_message_id = await start_turn(
        db,
        subject_id=subject_id,
        session_id=session_id,
        user_text=user_text,
    )
    await finish_turn(
        db,
        session_id=session_id,
        user_message_id=user_message_id,
        reply_text=reply_text,
        reasoning_content=reasoning_content,
        model_name=model_name,
        provider_name=provider_name,
        routing_reasoning_content=routing_reasoning_content,
        router_model_name=router_model_name,
        telemetry=telemetry,
    )
