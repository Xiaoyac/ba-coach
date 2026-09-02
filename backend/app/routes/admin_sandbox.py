"""Administrator-only module sandbox.

Each selection creates a brand-new conversation whose durable and in-memory
state both point at the requested module.  This is intentionally separate from
the normal programme router: it is a test harness for administrators, not a
way for ordinary users to skip BA progression gates.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..conversation_store import create_sandbox_conversation
from ..db import get_db
from ..identity import CallerIdentity, require_admin
from ..schemas import AdminSandboxConversation, AdminSandboxStartRequest
from ..session import SessionStore, get_session_store

router = APIRouter(prefix="/admin/sandbox", tags=["admin-sandbox"])


@router.post(
    "/module",
    response_model=AdminSandboxConversation,
    status_code=status.HTTP_201_CREATED,
)
async def start_module_sandbox(
    payload: AdminSandboxStartRequest,
    caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    store: SessionStore = Depends(get_session_store),
) -> AdminSandboxConversation:
    """Start the selected module with no transcript or progression memory."""
    session = await store.get_or_create(None)
    sandbox_memory = {
        "sandbox_mode": "true",
        "sandbox_start_module": payload.module,
    }
    await store.set_module(session.session_id, payload.module)
    await store.set_memory(session.session_id, sandbox_memory)

    try:
        conversation = await create_sandbox_conversation(
            db,
            subject_id=caller.subject_id,
            session_id=session.session_id,
            module=payload.module,
        )
    except Exception:
        await db.rollback()
        await store.reset(session.session_id)
        raise

    return AdminSandboxConversation(
        session_id=conversation.session_id,
        title=conversation.title,
        updated_at=conversation.updated_at,
        pinned=conversation.pinned,
        messages=[],
        next_module=payload.module,
    )
