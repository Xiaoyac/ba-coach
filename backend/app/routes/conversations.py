"""/api/conversations — the sidebar's history.

    POST   /api/conversations              create a conversation with its opening turn
    GET    /api/conversations              list, pinned first then most recent
    GET    /api/conversations/{session_id} one conversation's full transcript
    PATCH  /api/conversations/{session_id} rename and/or pin
    DELETE /api/conversations/{session_id} remove it

Conversations are *written* as a side effect of each turn in `routes/chat.py`
(via `app.conversation_store.record_turn`); everything here operates on them
after the fact, scoped to the authenticated caller (see `app.identity`).
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..conversation_store import create_conversation_with_opening
from ..db import get_db, get_sessionmaker
from ..identity import require_subject_id
from ..graph.nodes import wait_for_pending_routing
from ..models import (
    AIExecutionEvent,
    ClinicalRecordCycleLink,
    Conversation,
    ConversationMessage,
    ConversationModuleProgress,
    ConversationRuntimeState,
    PACycle,
)
from ..opening import OPENING_MESSAGE
from ..reasoning import normalize_reasoning_channels
from ..session import SessionStore, get_session_store
from ..schemas import (
    ConversationDetail,
    ConversationSummary,
    ConversationUpdate,
    Message,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _owned_or_404(
    db: AsyncSession, *, session_id: str, subject_id: str
) -> Conversation:
    """Fetch a conversation the caller owns, or 404.

    Deliberately the same 404 whether the row doesn't exist or belongs to a
    different subject — an id probe learns nothing either way. Shared by every
    single-conversation handler so that property can't drift between them.
    """
    result = await db.execute(
        select(Conversation).where(
            Conversation.session_id == session_id,
            Conversation.subject_id == subject_id,
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return conversation


def _summary(c: Conversation) -> ConversationSummary:
    return ConversationSummary(
        session_id=c.session_id,
        title=c.title,
        updated_at=c.updated_at,
        pinned=c.pinned,
    )


def _detail(c: Conversation, *, next_module: str | None = None) -> ConversationDetail:
    def project_message(message: ConversationMessage) -> Message:
        normalized = normalize_reasoning_channels(
            message.content, message.reasoning_content
        )
        return Message(
            role=message.role,
            content=normalized.reply,
            reasoning_content=normalized.reasoning or None,
            model_name=message.model_name,
            routing_reasoning_content=message.routing_reasoning_content,
            router_model_name=message.router_model_name,
        )

    return ConversationDetail(
        session_id=c.session_id,
        title=c.title,
        updated_at=c.updated_at,
        pinned=c.pinned,
        messages=[project_message(message) for message in c.messages],
        next_module=next_module,
    )


async def _detail_with_runtime(
    db: AsyncSession, conversation: Conversation
) -> ConversationDetail:
    """Build a transcript together with its durable graph module pointer."""
    runtime = await db.get(ConversationRuntimeState, conversation.id)
    return _detail(
        conversation, next_module=runtime.module if runtime else None
    )


@router.post("", response_model=ConversationDetail, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
    store: SessionStore = Depends(get_session_store),
) -> ConversationDetail:
    """Start a conversation and persist its opening before user input.

    The returned session id is immediately usable by ``/api/chat/stream``.
    Because the opening is in both stores, the first model turn sees what the
    user is answering and every later GET/refresh returns the same transcript.
    """
    session = await store.get_or_create(None)
    await store.append(session.session_id, OPENING_MESSAGE)
    try:
        conversation = await create_conversation_with_opening(
            db,
            subject_id=subject_id,
            session_id=session.session_id,
        )
    except Exception:
        await db.rollback()
        await store.reset(session.session_id)
        raise
    return await _detail_with_runtime(db, conversation)


async def _owned_revision(db: AsyncSession, *, session_id: str, subject_id: str):
    """Return ``(conversation_id, revision)`` without loading history."""
    return (
        await db.execute(
            select(
                Conversation.id,
                Conversation.revision,
            )
            .where(
                Conversation.session_id == session_id,
                Conversation.subject_id == subject_id,
            )
        )
    ).one_or_none()


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationSummary]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.subject_id == subject_id)
        # Pinned first, then by recency within each group.
        .order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
    )
    return [_summary(c) for c in result.scalars().all()]


@router.get("/{session_id}", response_model=ConversationDetail)
async def get_conversation(
    session_id: str,
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> ConversationDetail:
    conversation = await _owned_or_404(
        db, session_id=session_id, subject_id=subject_id
    )
    return await _detail_with_runtime(db, conversation)


@router.get("/{session_id}/revision", response_model=dict[str, int])
async def get_conversation_revision(
    session_id: str,
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Cheap reconnect fallback for proxies that silently stall SSE streams."""
    row = await _owned_revision(db, session_id=session_id, subject_id=subject_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return {"revision": int(row[1])}


@router.get("/{session_id}/events")
async def conversation_events(
    session_id: str,
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Live snapshots for the conversation currently open on another device.

    This intentionally polls the durable database inside one long-lived SSE
    response rather than using an in-process pub/sub list. It therefore still
    works when the next deployment has multiple API workers: whichever worker
    accepted the chat writes MySQL, and whichever worker owns this stream sees
    the new auto-increment message id on its next sub-second check.
    """
    # Fail with a normal 404 before response headers are sent. A deletion that
    # happens later is delivered in-band as a `deleted` event.
    await _owned_or_404(db, session_id=session_id, subject_id=subject_id)
    # The request-scoped session lives as long as the streaming response.
    # Release its verification transaction now so every open browser tab does
    # not pin one otherwise-idle MySQL connection for hours.
    await db.rollback()

    async def event_source():
        last_message_id: int | None = None
        idle_ticks = 0
        while True:
            async with get_sessionmaker()() as live_db:
                # Check only the monotonic revision first. It changes for both
                # appended messages and delayed router enrichment of an
                # existing assistant row.
                revision_row = await _owned_revision(
                    live_db, session_id=session_id, subject_id=subject_id
                )

                if revision_row is None:
                    yield "event: deleted\ndata: {}\n\n"
                    return

                revision = int(revision_row[1])
                if last_message_id != revision:
                    last_message_id = revision
                    idle_ticks = 0
                    conversation = (
                        await live_db.execute(
                            select(Conversation).where(
                                Conversation.id == revision_row[0]
                            )
                        )
                    ).scalar_one()
                    payload = (
                        await _detail_with_runtime(live_db, conversation)
                    ).model_dump(mode="json")
                    yield (
                        "event: snapshot\ndata: "
                        + json.dumps(payload, ensure_ascii=False)
                        + "\n\n"
                    )
                else:
                    idle_ticks += 1
                    # A comment keeps proxies from considering an otherwise
                    # quiet stream abandoned without waking the React client.
                    if idle_ticks >= 20:
                        idle_ticks = 0
                        yield ": keep-alive\n\n"

            await asyncio.sleep(0.75)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/{session_id}", response_model=ConversationSummary)
async def update_conversation(
    session_id: str,
    payload: ConversationUpdate,
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> ConversationSummary:
    """Rename and/or pin. Omitted fields are left untouched.

    `updated_at` is deliberately *not* bumped here: the sidebar sorts on it as
    "last talked to", and renaming or pinning a conversation is not talking to
    it. Bumping it would shuffle an untouched conversation to the top of the
    list purely because it was relabelled.
    """
    conversation = await _owned_or_404(
        db, session_id=session_id, subject_id=subject_id
    )

    if payload.title is None and payload.pinned is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one of: title, pinned",
        )

    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="title cannot be empty or whitespace",
            )
        conversation.title = title

    if payload.pinned is not None:
        conversation.pinned = payload.pinned

    await db.commit()
    return _summary(conversation)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    session_id: str,
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
    store: SessionStore = Depends(get_session_store),
) -> None:
    """Remove a conversation, and end the live session behind it.

    Deleting the rows is not enough on its own. The transcript log and the
    live LangGraph session are separate stores (see `app.models`), and this
    used to touch only the first — on the assumption that a stray in-memory
    session was harmless because the client would drop the id.

    It is not harmless. Anything still holding that id — the other device in a
    two-device session, a tab that has not refreshed — sends it with its next
    message; the store still recognises it, so the id survives and
    `record_turn` writes the row straight back. The conversation reappears in
    the sidebar, and the delete looks like it silently failed.

    Resetting the session is what makes the id genuinely dead: the next
    message from any client, on any device, starts a new conversation instead
    of resurrecting this one. The messages go with the row through the
    relationship's `delete-orphan` cascade.
    """
    # Serialize deletion with the delayed router. Otherwise a user can delete
    # immediately after receiving a reply while that task is still enriching
    # the same row, producing a commit race (and on SQLite, a broken shared
    # transaction). No model call runs while we hold this lock: the router
    # acquires it only after its slow call has completed.
    # Let an already-running router finish before deleting. Cancelling while
    # it owns a database connection can invalidate a single-connection SQLite
    # deployment; awaiting also guarantees it cannot recreate live state after
    # this endpoint resets the session.
    await wait_for_pending_routing(session_id)
    turn_lock = await store.get_turn_lock(session_id)
    async with turn_lock:
        conversation = await _owned_or_404(
            db, session_id=session_id, subject_id=subject_id
        )
        cycle_ids = select(PACycle.id).where(PACycle.conversation_id == conversation.id)
        # Explicit child deletes make the endpoint deterministic even in local
        # SQLite where foreign-key cascades may be disabled. The externally
        # owned clinical rows are intentionally retained; only their app-owned
        # conversation/cycle links are removed.
        await db.execute(
            delete(ClinicalRecordCycleLink).where(
                ClinicalRecordCycleLink.cycle_id.in_(cycle_ids)
            )
        )
        await db.execute(
            delete(AIExecutionEvent).where(
                AIExecutionEvent.conversation_id == conversation.id
            )
        )
        await db.execute(
            delete(ConversationModuleProgress).where(
                ConversationModuleProgress.conversation_id == conversation.id
            )
        )
        await db.execute(delete(PACycle).where(PACycle.conversation_id == conversation.id))
        await db.execute(
            delete(ConversationRuntimeState).where(
                ConversationRuntimeState.conversation_id == conversation.id
            )
        )
        await db.execute(
            delete(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id
            )
        )
        await db.execute(delete(Conversation).where(Conversation.id == conversation.id))
        await db.commit()
        await store.reset(session_id)
