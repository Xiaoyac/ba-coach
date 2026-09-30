"""Owned snapshot publication and token-scoped, read-only public viewing."""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..graph.nodes import has_pending_routing
from ..identity import require_subject_id
from ..knowledge_references import KnowledgeReferences
from ..models import AIExecutionEvent, Conversation, ConversationShare
from ..session import SessionStore, get_session_store
from ..share_schemas import (
    ConversationShareCreated,
    ConversationShareSnapshot,
    SharedMessage,
)
from .conversations import _detail, _owned_or_404

router = APIRouter(tags=["conversation shares"])
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _headers(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"


def _busy() -> HTTPException:
    return HTTPException(status_code=409, detail="这段对话仍在生成或保存，请完成后再创建分享。")


@router.post("/conversations/{session_id}/shares", response_model=ConversationShareCreated, status_code=201)
async def create_share(
    session_id: str,
    response: Response,
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
    store: SessionStore = Depends(get_session_store),
) -> ConversationShareCreated:
    _headers(response)
    await _owned_or_404(db, session_id=session_id, subject_id=subject_id)
    # Drop the first read transaction before reloading under the publication
    # lock. MySQL repeatable-read must not return the pre-lock transcript.
    await db.rollback()
    lock = await store.get_turn_lock(session_id)
    if lock.locked() or has_pending_routing(session_id):
        raise _busy()
    async with lock:
        if has_pending_routing(session_id):
            raise _busy()
        conversation = (await db.execute(select(Conversation).where(
            Conversation.session_id == session_id,
            Conversation.subject_id == subject_id,
        ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        # User input is durable before chat takes its turn lock. This also
        # detects a still-running generation on another API worker.
        if conversation.messages and conversation.messages[-1].role == "user":
            raise HTTPException(status_code=409, detail=(
                "最后一条用户消息还没有已保存的回复；若该轮已停止或失败，请完成下一轮后再分享。"
            ))
        if not conversation.messages:
            raise HTTPException(status_code=409, detail="对话还没有可分享的消息。")
        assistant_ids = [m.id for m in conversation.messages if m.role == "assistant"]
        references: dict[int, KnowledgeReferences] = {}
        if assistant_ids:
            events = (await db.execute(select(
                AIExecutionEvent.assistant_message_id, AIExecutionEvent.event_metadata,
            ).where(
                AIExecutionEvent.conversation_id == conversation.id,
                AIExecutionEvent.session_id == session_id,
                AIExecutionEvent.subject_id == subject_id,
                AIExecutionEvent.stage == "main_generation",
                AIExecutionEvent.assistant_message_id.in_(assistant_ids),
            ).order_by(AIExecutionEvent.id.desc()))).all()
            for message_id, metadata in events:
                if message_id in references:
                    continue
                try:
                    # Never copy the whole telemetry object: it also contains
                    # prompts, memory and account state outside this share.
                    raw = metadata.get("knowledge_references") if isinstance(metadata, dict) else None
                    references[message_id] = KnowledgeReferences.model_validate(raw or {})
                except ValidationError:
                    references[message_id] = KnowledgeReferences()
        from ..request_records import request_records
        requests = await request_records(db, conversation)
        created_at = datetime.now(timezone.utc)
        snapshot = ConversationShareSnapshot(
            title=conversation.title,
            created_at=created_at,
            messages=[SharedMessage(
                **{**message.model_dump(), "id": index},
                request_records=requests.get(message.id),
                knowledge_references=(references.get(message.id, KnowledgeReferences())
                                      if message.role == "assistant" else None),
            ) for index, message in enumerate(_detail(conversation).messages, start=1)],
        )
        token = secrets.token_urlsafe(32)
        share = ConversationShare(
            id=str(uuid4()), conversation_id=conversation.id,
            token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
            snapshot=snapshot.model_dump(mode="json"), created_at=created_at,
        )
        db.add(share)
        await db.commit()
        return ConversationShareCreated(
            id=share.id, title=snapshot.title, created_at=created_at,
            message_count=len(snapshot.messages), token=token, path=f"/share/{token}",
        )


@router.get("/shares/{token}", response_model=ConversationShareSnapshot)
async def get_share(token: str, db: AsyncSession = Depends(get_db)) -> Response:
    headers = {
        "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
    }
    if not TOKEN_PATTERN.fullmatch(token):
        raise HTTPException(status_code=404, detail="Share not found", headers=headers)
    share = (await db.execute(select(ConversationShare).join(
        Conversation, Conversation.id == ConversationShare.conversation_id,
    ).where(
        ConversationShare.token_digest == hashlib.sha256(token.encode("ascii")).hexdigest(),
        # Preserve invalidation of links revoked before revocation was removed.
        ConversationShare.revoked_at.is_(None),
    ))).scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found", headers=headers)
    # Validate even persisted JSON against the explicit public allowlist.
    snapshot = ConversationShareSnapshot.model_validate(share.snapshot)
    return Response(content=snapshot.model_dump_json(), media_type="application/json", headers=headers)
