"""Module-scoped lexical retrieval over the shared BA Coach knowledge base.

The production corpus is intentionally kept in MySQL rather than an external
vector service. Chinese bi/trigrams, English terms, and a small bilingual query
expander are enough for the project's curated BA/PA/BCT/MI documents while
keeping retrieval deterministic and auditable.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from time import monotonic
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .db import get_sessionmaker
from .knowledge_store import (
    IGNORED_KNOWLEDGE_SOURCE_NAMES,
    KNOWLEDGE_CATEGORY_MODULES,
)
from .models import KnowledgeChunkRecord, KnowledgeSourceRecord

logger = logging.getLogger(__name__)

_INDEX_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    text: str
    source: str
    score: float = 0.0


@dataclass(frozen=True)
class _IndexedKnowledgeChunk:
    id: int
    source_id: int
    source_name: str
    category: str
    heading: str
    content: str
    terms: Counter[str]
    heading_terms: Counter[str]


class KnowledgeBase(Protocol):
    async def search(
        self, *, module: str, query: str, top_k: int = 3
    ) -> list[KnowledgeChunk]:
        """Return the most relevant chunks for `query` within `module`."""
        ...


# Seed corpus, keyed by module. Replace with your Coze KB export.
SEED_DOCUMENTS: dict[str, list[tuple[str, str]]] = {
    "module_1": [
        (
            "intake-open-questions",
            "Open questions invite elaboration: 'what has that been like "
            "for you', 'tell me more about that'. Closed questions collect "
            "facts but stall exploration early in a conversation.",
        ),
        (
            "intake-reflective-listening",
            "Reflective listening restates the speaker's meaning in fresh "
            "words. It signals accurate understanding and lets the person "
            "correct the record before the conversation moves on.",
        ),
    ],
    "module_2": [
        (
            "assessment-dimensions",
            "Structured assessment covers onset, duration, frequency, "
            "intensity, and functional impact across work, sleep, "
            "relationships, and self-care.",
        ),
        (
            "assessment-pacing",
            "Ask one dimension at a time. Rapid-fire questioning reads as an "
            "interrogation and reduces disclosure.",
        ),
    ],
    "module_3": [
        (
            "technique-thought-record",
            "A thought record captures situation, automatic thought, emotion "
            "and intensity, evidence for and against, and a balanced "
            "alternative thought.",
        ),
        (
            "technique-grounding",
            "The 5-4-3-2-1 grounding exercise directs attention to five "
            "things seen, four heard, three touched, two smelled, one "
            "tasted. It interrupts rumination by re-anchoring attention.",
        ),
        (
            "technique-behavioral-activation",
            "Behavioral activation schedules small, achievable, valued "
            "activities to break the withdrawal-low-mood cycle. Start "
            "smaller than feels necessary.",
        ),
    ],
    "module_4": [
        (
            "closing-consolidation",
            "Closing a session well means naming what was covered, what the "
            "person noticed, and one concrete next step they chose "
            "themselves rather than one that was assigned.",
        ),
        (
            "closing-continuity",
            "Continuity notes carry forward the agreed next step and the "
            "language the person used for their own experience, so the next "
            "conversation resumes rather than restarts.",
        ),
    ],
}

_TOKEN = re.compile(r"[a-z0-9]+|[一-鿿]")


def _tokenize(text: str) -> set[str]:
    """Latin words plus individual CJK characters — no tokenizer dependency."""
    return set(_TOKEN.findall(text.lower()))


_LATIN_OR_CJK = re.compile(r"[a-z0-9]+|[一-鿿]+")


# The PA compendium and most PA papers use English activity names, while the
# coach's users normally write in Chinese. This is query expansion, not a
# clinical classifier: matching rows remain visible to the model and the model
# still applies the PA definition in the module prompt.
_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "身体活动": ("physical activity", "exercise"),
    "运动": ("physical activity", "exercise"),
    "锻炼": ("exercise", "training"),
    "散步": ("walking",),
    "走路": ("walking",),
    "步行": ("walking",),
    "遛狗": ("walking dog", "walking"),
    "跑步": ("running", "jogging"),
    "慢跑": ("jogging",),
    "骑车": ("bicycling", "cycling"),
    "自行车": ("bicycling", "cycling"),
    "游泳": ("swimming",),
    "羽毛球": ("badminton",),
    "篮球": ("basketball",),
    "足球": ("soccer", "football"),
    "乒乓球": ("table tennis",),
    "网球": ("tennis",),
    "瑜伽": ("yoga",),
    "太极": ("tai chi",),
    "跳舞": ("dancing", "dance"),
    "舞蹈": ("dancing", "dance"),
    "爬山": ("hiking", "climbing"),
    "徒步": ("hiking", "walking"),
    "爬楼": ("stair climbing", "stairs"),
    "跳绳": ("rope jumping", "jumping rope"),
    "健身": ("conditioning exercise", "gym exercise"),
    "力量训练": ("resistance training", "weight lifting"),
    "举重": ("weight lifting",),
    "划船": ("rowing",),
    "滑冰": ("skating",),
    "滑雪": ("skiing",),
    "普拉提": ("pilates",),
    "家务": ("home activities", "housework"),
    "打扫": ("cleaning", "housework"),
    "拖地": ("mopping", "cleaning"),
    "园艺": ("lawn and garden", "gardening"),
    "逛街": ("shopping", "walking"),
    "久坐": ("sedentary behavior", "sitting"),
    "抑郁": ("depression", "depressive"),
    "情绪低落": ("depression", "low mood"),
    "焦虑": ("anxiety",),
    "幸福感": ("well-being", "happiness"),
    "强度": ("intensity", "vigorous", "moderate", "light"),
    "时长": ("duration",),
    "睡眠": ("sleep",),
    "认知": ("cognitive", "cognition"),
    "青少年": ("adolescent", "youth"),
    "儿童": ("children",),
    "精神分裂": ("schizophrenia",),
    "炎症": ("inflammation", "inflammatory"),
}


def expand_knowledge_query(query: str) -> str:
    """Append bilingual aliases relevant to the user's literal query."""
    lowered = query.lower()
    additions: list[str] = []
    for trigger, aliases in _QUERY_EXPANSIONS.items():
        if trigger in lowered:
            additions.extend(aliases)
    return " ".join((query, *dict.fromkeys(additions))).strip()


def _search_terms(text: str) -> Counter[str]:
    """Create useful Chinese bi/trigrams without adding a tokenizer package."""
    terms: Counter[str] = Counter()
    for token in _LATIN_OR_CJK.findall(text.lower()):
        if re.fullmatch(r"[a-z0-9]+", token):
            if len(token) > 1:
                terms[token] += 1
            continue
        for size in (2, 3):
            for index in range(max(0, len(token) - size + 1)):
                terms[token[index : index + size]] += 1
    return terms


class StubKnowledgeBase:
    """Keyword-overlap search. Deterministic, offline, dependency-free."""

    def __init__(self, documents: dict[str, list[tuple[str, str]]] | None = None):
        self._documents = documents if documents is not None else SEED_DOCUMENTS

    async def search(
        self, *, module: str, query: str, top_k: int = 3
    ) -> list[KnowledgeChunk]:
        query_tokens = _tokenize(query)
        scored: list[KnowledgeChunk] = []
        for doc_id, text in self._documents.get(module, []):
            overlap = query_tokens & _tokenize(text)
            score = len(overlap) / len(query_tokens) if query_tokens else 0.0
            scored.append(
                KnowledgeChunk(id=doc_id, text=text, source=module, score=round(score, 4))
            )

        scored.sort(key=lambda chunk: (-chunk.score, chunk.id))
        # Return nothing rather than something irrelevant. The previous
        # behaviour ("always hand back at least one chunk so the block is
        # never empty") is actively harmful with this corpus: `_tokenize`
        # splits Latin words and CJK characters into disjoint sets, so a
        # Chinese turn overlaps with English seed text *never* — every score
        # is 0, and the fallback then injected whichever chunk sorted first
        # alphabetically, labelled to the model as authoritative "Retrieved
        # Knowledge". Module prompts that say 调用知识库 were therefore being
        # pointed at generic English counselling boilerplate on every single
        # turn. An absent block is honest; a misleading one is not.
        return [chunk for chunk in scored if chunk.score > 0][:top_k]


class DatabaseKnowledgeBase:
    """Module-filtered lexical retrieval over administrator-imported sources.

    The storage/API boundary is intentionally independent from the ranking
    implementation. A vector index can replace this class later without
    changing the graph, database import format, or admin endpoints.
    """

    def __init__(self, sessionmaker_provider: Callable = get_sessionmaker):
        self._sessionmaker_provider = sessionmaker_provider
        self._index: tuple[_IndexedKnowledgeChunk, ...] | None = None
        self._index_loaded_at = 0.0
        self._index_lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Make the next search observe newly imported administrator content."""
        self._index = None
        self._index_loaded_at = 0.0

    async def warmup(self) -> int:
        """Build the in-process lexical index before the first user turn."""
        index = await self._load_index(force=True)
        return len(index)

    async def _load_index(
        self, *, force: bool = False
    ) -> tuple[_IndexedKnowledgeChunk, ...]:
        now = monotonic()
        if (
            not force
            and self._index is not None
            and now - self._index_loaded_at < _INDEX_TTL_SECONDS
        ):
            return self._index
        async with self._index_lock:
            now = monotonic()
            if (
                not force
                and self._index is not None
                and now - self._index_loaded_at < _INDEX_TTL_SECONDS
            ):
                return self._index
            async with self._sessionmaker_provider()() as db:
                rows = (
                    await db.execute(
                        select(KnowledgeChunkRecord, KnowledgeSourceRecord)
                        .join(
                            KnowledgeSourceRecord,
                            KnowledgeSourceRecord.id == KnowledgeChunkRecord.source_id,
                        )
                        .where(
                            KnowledgeSourceRecord.name.notin_(
                                IGNORED_KNOWLEDGE_SOURCE_NAMES
                            )
                        )
                    )
                ).all()
            self._index = tuple(
                _IndexedKnowledgeChunk(
                    id=row.id,
                    source_id=source.id,
                    source_name=source.name,
                    category=source.category,
                    heading=row.heading,
                    content=row.content,
                    terms=_search_terms(
                        f"{source.name} {row.heading} {row.content}"
                    ),
                    heading_terms=_search_terms(f"{source.name} {row.heading}"),
                )
                for row, source in rows
            )
            self._index_loaded_at = monotonic()
            logger.info("knowledge index loaded: %d chunks", len(self._index))
            return self._index

    async def search(
        self, *, module: str, query: str, top_k: int = 3
    ) -> list[KnowledgeChunk]:
        allowed = [
            category
            for category, modules in KNOWLEDGE_CATEGORY_MODULES.items()
            if module in modules
        ]
        if not allowed or not query.strip() or top_k <= 0:
            return []
        try:
            index = await self._load_index()
        except SQLAlchemyError:
            logger.exception("knowledge retrieval failed for %s", module)
            return []

        documents = [document for document in index if document.category in allowed]
        query_terms = _search_terms(expand_knowledge_query(query))
        if not query_terms or not documents:
            return []
        document_count = len(documents)
        frequencies = Counter(
            term
            for document in documents
            for term in set(document.terms)
            if term in query_terms
        )
        scored: list[tuple[KnowledgeChunk, str, int]] = []
        for document in documents:
            terms = document.terms
            common = set(query_terms) & set(terms)
            if not common:
                continue
            score = 0.0
            for term in common:
                inverse_frequency = math.log(
                    1 + (document_count - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
                )
                term_frequency = terms[term] / (terms[term] + 1.2)
                title_boost = 1.45 if term in document.heading_terms else 1.0
                score += inverse_frequency * term_frequency * title_boost
            coverage = len(common) / len(query_terms)
            score *= 0.5 + coverage
            chunk = KnowledgeChunk(
                id=f"kb:{document.id}",
                text=document.content,
                source=(
                    f"{document.source_name} · {document.heading}"
                    if document.heading
                    else document.source_name
                ),
                score=round(score, 4),
            )
            scored.append((chunk, document.category, document.source_id))
        scored.sort(key=lambda item: (-item[0].score, item[0].id))

        # Modules 2 and 3 each draw from three knowledge families. A very long
        # book must not occupy every slot just because it contains a common
        # word many times, so take the strongest matching chunk per category
        # first, then fill the remaining budget by global score.
        selected: list[KnowledgeChunk] = []
        selected_ids: set[str] = set()
        used_categories: set[str] = set()
        for chunk, category, _source_id in scored:
            if category in used_categories:
                continue
            selected.append(chunk)
            selected_ids.add(chunk.id)
            used_categories.add(category)
            if len(selected) == top_k:
                return selected
        for chunk, _category, _source_id in scored:
            if chunk.id in selected_ids:
                continue
            selected.append(chunk)
            if len(selected) == top_k:
                break
        return selected


_knowledge_base: KnowledgeBase | None = None


def get_knowledge_base() -> KnowledgeBase:
    global _knowledge_base
    if _knowledge_base is None:
        _knowledge_base = DatabaseKnowledgeBase()
    return _knowledge_base


def invalidate_knowledge_cache() -> None:
    """Invalidate the singleton after an administrator replaces a source."""
    if isinstance(_knowledge_base, DatabaseKnowledgeBase):
        _knowledge_base.invalidate()


async def warm_knowledge_base() -> int:
    """Best-effort startup warmup for the production database index."""
    knowledge = get_knowledge_base()
    if isinstance(knowledge, DatabaseKnowledgeBase):
        return await knowledge.warmup()
    return 0
