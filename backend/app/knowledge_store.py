"""Persistent shared knowledge documents and Markdown chunking."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import KnowledgeChunkRecord, KnowledgeSourceRecord, _utcnow

KNOWLEDGE_CATEGORY_MODULES: dict[str, tuple[str, ...]] = {
    "BA": ("module_1", "module_2", "module_3", "module_4"),
    "PA": ("module_2",),
    "BCT": ("module_3",),
    "MI": ("module_2", "module_3"),
}

# Historical aliases of the two BA files in ``KnowledgeBase``. Keep the rows
# for audit/recovery, but never index them: their 160 chunks are exact
# duplicates of the Chinese-named sources deployed from the project folder.
IGNORED_KNOWLEDGE_SOURCE_NAMES = frozenset(
    {"BA-chapters-1-6.md", "BA-chapters-7-9.md"}
)

_HEADING = re.compile(r"^\s*\\?#{1,6}\s+(.+?)\s*$")


@dataclass(frozen=True)
class ParsedKnowledgeChunk:
    heading: str
    content: str


@dataclass(frozen=True)
class KnowledgeImportResult:
    source: KnowledgeSourceRecord
    chunk_count: int
    unchanged: bool


async def knowledge_revision(db: AsyncSession) -> str:
    """Small shared manifest, visible to every worker after an import commits.

    Imports replace chunks and update their source in one transaction. No
    schema migration, shared process memory or polling window is required.
    Direct chunk SQL edits must also update the parent source metadata.
    """
    # Chunk identity/count also changes when identical content is re-imported
    # after an intervening version, even on second-precision MySQL timestamps.
    chunks = (select(KnowledgeChunkRecord.source_id,
                     func.count(KnowledgeChunkRecord.id).label("chunk_count"),
                     func.max(KnowledgeChunkRecord.id).label("last_id"))
              .group_by(KnowledgeChunkRecord.source_id).subquery())
    rows = (await db.execute(
        select(KnowledgeSourceRecord.id, KnowledgeSourceRecord.name,
               KnowledgeSourceRecord.category, KnowledgeSourceRecord.content_hash,
               KnowledgeSourceRecord.updated_at, chunks.c.chunk_count, chunks.c.last_id)
        .outerjoin(chunks, chunks.c.source_id == KnowledgeSourceRecord.id)
        .where(KnowledgeSourceRecord.name.notin_(IGNORED_KNOWLEDGE_SOURCE_NAMES))
        .order_by(KnowledgeSourceRecord.id)
    )).all()
    manifest = [[row.id, row.name, row.category, row.content_hash,
                 row.updated_at.isoformat() if row.updated_at else None,
                 row.chunk_count, row.last_id] for row in rows]
    return hashlib.sha256(json.dumps(manifest, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _split_body(body: str, *, max_chars: int, overlap: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            start = 0
            while start < len(paragraph):
                end = min(start + max_chars, len(paragraph))
                pieces.append(paragraph[start:end].strip())
                if end == len(paragraph):
                    break
                start = max(end - overlap, start + 1)
            continue
        candidate = f"{current}\n\n{paragraph}".strip()
        if current and len(candidate) > max_chars:
            pieces.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def chunk_markdown(
    markdown: str, *, max_chars: int = 1400, overlap: int = 160
) -> list[ParsedKnowledgeChunk]:
    """Split normal or Coze-escaped Markdown headings into prompt-sized chunks."""
    sections: list[tuple[str, str]] = []
    heading = ""
    lines: list[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            sections.append((heading, body))

    for raw_line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _HEADING.match(raw_line)
        if match:
            flush()
            heading = match.group(1).strip()
            lines = []
        else:
            lines.append(raw_line)
    flush()

    chunks: list[ParsedKnowledgeChunk] = []
    for section_heading, body in sections:
        for piece in _split_body(body, max_chars=max_chars, overlap=overlap):
            content = f"# {section_heading}\n\n{piece}" if section_heading else piece
            chunks.append(
                ParsedKnowledgeChunk(heading=section_heading, content=content.strip())
            )
    return chunks


async def import_knowledge_source(
    db: AsyncSession,
    *,
    name: str,
    category: str,
    markdown: str,
    updated_by: str,
) -> KnowledgeImportResult:
    """Create or atomically replace one global source, preserving its identity."""
    clean_name = name.strip()
    clean_category = category.strip().upper()
    if clean_category not in KNOWLEDGE_CATEGORY_MODULES:
        raise ValueError(f"unsupported knowledge category: {category}")
    if not clean_name:
        raise ValueError("knowledge source name cannot be blank")
    chunks = chunk_markdown(markdown)
    if not chunks:
        raise ValueError("knowledge source contains no readable text")

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    source = (
        await db.execute(
            select(KnowledgeSourceRecord)
            .where(KnowledgeSourceRecord.name == clean_name)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if source and source.content_hash == content_hash and source.category == clean_category:
        count = (
            await db.execute(
                select(func.count(KnowledgeChunkRecord.id)).where(
                    KnowledgeChunkRecord.source_id == source.id
                )
            )
        ).scalar_one()
        return KnowledgeImportResult(source=source, chunk_count=count, unchanged=True)

    if source is None:
        source = KnowledgeSourceRecord(
            name=clean_name,
            category=clean_category,
            content_hash=content_hash,
            updated_by=updated_by,
        )
        db.add(source)
        await db.flush()
    else:
        await db.execute(
            delete(KnowledgeChunkRecord).where(
                KnowledgeChunkRecord.source_id == source.id
            )
        )
        source.category = clean_category
        source.content_hash = content_hash
        source.updated_by = updated_by
        source.updated_at = _utcnow()

    db.add_all(
        KnowledgeChunkRecord(
            source_id=source.id,
            ordinal=index,
            heading=chunk.heading[:512],
            content=chunk.content,
        )
        for index, chunk in enumerate(chunks)
    )
    await db.flush()
    return KnowledgeImportResult(source=source, chunk_count=len(chunks), unchanged=False)
