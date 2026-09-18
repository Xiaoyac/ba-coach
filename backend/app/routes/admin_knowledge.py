"""Administrator-only API for the shared coaching knowledge base."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..account_identity import audit_identity
from ..db import get_db
from ..identity import CallerIdentity, require_admin
from ..knowledge_store import KNOWLEDGE_CATEGORY_MODULES, import_knowledge_source
from ..models import KnowledgeChunkRecord, KnowledgeSourceRecord
from ..retrieval import DatabaseKnowledgeBase, get_knowledge_base, invalidate_knowledge_cache
from ..providers.base import ProviderError
from ..schemas import (
    AdminKnowledgeBundle,
    AdminKnowledgeImport,
    AdminKnowledgeSourceItem,
)

router = APIRouter(prefix="/admin/knowledge", tags=["admin-knowledge"])


@router.get("/cache-stats")
async def cache_stats(response: Response, _caller: CallerIdentity = Depends(require_admin)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        knowledge = get_knowledge_base()
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail="检索缓存统计暂不可用") from exc
    if not isinstance(knowledge, DatabaseKnowledgeBase):
        raise HTTPException(status_code=503, detail="当前检索器不支持缓存统计")
    return knowledge.cache_monitoring_stats()


def _item(
    source: KnowledgeSourceRecord, chunk_count: int, *, unchanged: bool = False
) -> AdminKnowledgeSourceItem:
    return AdminKnowledgeSourceItem(
        id=source.id,
        name=source.name,
        category=source.category,
        modules=list(KNOWLEDGE_CATEGORY_MODULES[source.category]),
        chunk_count=chunk_count,
        content_hash=source.content_hash,
        updated_by=source.updated_by,
        updated_at=source.updated_at,
        unchanged=unchanged,
    )


@router.get("", response_model=AdminKnowledgeBundle)
async def list_knowledge_sources(
    _caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminKnowledgeBundle:
    count = (
        select(
            KnowledgeChunkRecord.source_id,
            func.count(KnowledgeChunkRecord.id).label("chunk_count"),
        )
        .group_by(KnowledgeChunkRecord.source_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(KnowledgeSourceRecord, func.coalesce(count.c.chunk_count, 0))
            .outerjoin(count, count.c.source_id == KnowledgeSourceRecord.id)
            .order_by(KnowledgeSourceRecord.name)
        )
    ).all()
    return AdminKnowledgeBundle(
        sources=[_item(source, int(chunk_count)) for source, chunk_count in rows]
    )


@router.post("", response_model=AdminKnowledgeSourceItem, status_code=status.HTTP_201_CREATED)
async def import_knowledge(
    payload: AdminKnowledgeImport,
    caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminKnowledgeSourceItem:
    editor = await audit_identity(db, caller.account)
    try:
        result = await import_knowledge_source(
            db,
            name=payload.name,
            category=payload.category,
            markdown=payload.markdown,
            updated_by=editor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    # Only committed changes invalidate. Other workers observe the source
    # manifest on their next retrieval; an identical import keeps warm caches.
    if not result.unchanged:
        invalidate_knowledge_cache()
    await db.refresh(result.source)
    return _item(result.source, result.chunk_count, unchanged=result.unchanged)
