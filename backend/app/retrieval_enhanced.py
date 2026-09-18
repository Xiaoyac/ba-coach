"""Evidence-first BM25F retrieval, independent of evaluation labels and case IDs.

The original retriever remains the reproducible baseline. This ranker adds field
length normalization, explicit heading matches, negative activity handling, and
an adaptive relevance cutoff. It never changes clinical/goal state.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
import unicodedata

from .knowledge_store import KNOWLEDGE_CATEGORY_MODULES
from .retrieval import (KnowledgeChunk, _QUERY_EXPANSIONS, _NON_EVIDENCE_TERMS,
                        _search_terms, expand_knowledge_query)

VERSION = "bm25f-evidence-v1"
_FILLERS = _NON_EVIDENCE_TERMS | frozenset(
    "请问 告诉 介绍 解释 具体 详细 一定 怎样 是否 是不是 属于 有哪些 什么意思 "
    "用来 做到 一起 一点 这个活动 这项活动 那个活动".split())


def focus_query(query: str) -> str:
    """Preserve context for references, but do not expand explicitly rejected activities."""
    normalized = unicodedata.normalize("NFKC", query).casefold().strip()[:6000]
    first = normalized.split("\n", 1)[0]
    rejected = set()
    for activity in _QUERY_EXPANSIONS:
        if re.search(r"(?:不想|不打算|不再|不要|不愿|放弃|停止)[^,，。！？!?;；\n]{0,8}" + re.escape(activity), first):
            rejected.add(activity)
    # Rejected concepts are not positive retrieval terms, including stale history.
    for activity in sorted(rejected, key=len, reverse=True):
        normalized = normalized.replace(activity, " ")
    return normalized


def query_terms(text: str) -> set[str]:
    for phrase in sorted(_FILLERS, key=lambda s: (-len(s), s)):
        if phrase.isascii():
            text = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", text)
        else:
            text = text.replace(phrase, " ")
    return set(_search_terms(text))


@dataclass(frozen=True)
class EnhancedPolicy:
    min_coverage: float = 0.25
    relative_score: float = 0.55
    min_score: float = 0.1
    max_per_source: int = 2
    heading_weight: float = 2.5
    exact_heading_boost: float = 1.8

    def __post_init__(self):
        if not 0 <= self.min_coverage <= 1 or not 0 <= self.relative_score <= 1:
            raise ValueError("Invalid relevance threshold")
        if self.max_per_source < 1 or not math.isfinite(self.min_score) or self.min_score < 0:
            raise ValueError("Invalid result policy")
        if any(not math.isfinite(value) or value <= 0 for value in
               (self.heading_weight, self.exact_heading_boost)):
            raise ValueError("Heading weights must be finite and positive")


@dataclass(frozen=True)
class EvidenceCandidate:
    chunk: KnowledgeChunk
    source_id: int
    category: str
    coverage: float
    heading_exact: bool


class EnhancedKnowledgeRanker:
    def __init__(self, corpus, policy: EnhancedPolicy | None = None):
        self.corpus = tuple(corpus)
        self.policy = policy or EnhancedPolicy()
        self.by_id = {f"kb:{d.id}": d for d in self.corpus}
        if len(self.by_id) != len(self.corpus):
            raise ValueError("Duplicate knowledge IDs")
        self._body = {d.id: _search_terms(d.content) for d in self.corpus}
        self._heading = {d.id: _search_terms(d.heading) for d in self.corpus}
        self._scopes = {}
        for module in {m for modules in KNOWLEDGE_CATEGORY_MODULES.values() for m in modules}:
            docs = tuple(d for d in self.corpus if module in KNOWLEDGE_CATEGORY_MODULES.get(d.category, ()))
            df = Counter(t for d in docs for t in (self._body[d.id].keys() | self._heading[d.id].keys()))
            average = sum(sum(self._body[d.id].values()) for d in docs) / max(1, len(docs))
            self._scopes[module] = (docs, df, average)

    def candidates(self, *, module: str, query: str, limit: int = 32) -> list[EvidenceCandidate]:
        docs, df, average = self._scopes.get(module, ((), {}, 1))
        if not docs or not query.strip() or limit <= 0:
            return []
        focused = focus_query(query)
        expanded = expand_knowledge_query(focused)
        terms = query_terms(expanded)
        if not terms:
            return []
        aliases = query_terms(expanded[len(focused):])
        idf = {t: math.log(1 + (len(docs) - df.get(t, 0) + .5) / (df.get(t, 0) + .5)) for t in terms}
        total = sum(idf[t] for t in sorted(terms))
        alias_total = sum(idf[t] for t in sorted(aliases))
        compact_query = re.sub(r"\W+", "", focused)
        ranked = []
        for doc in docs:
            body, heading = self._body[doc.id], self._heading[doc.id]
            common = terms.intersection(body.keys() | heading.keys())
            if not common:
                continue
            normalization = 1.2 * (.35 + .65 * sum(body.values()) / max(1, average))
            score = sum(idf[t] * (body[t] * 2.2 / (body[t] + normalization)
                + self.policy.heading_weight * int(t in heading)) for t in sorted(common))
            coverage = sum(idf[t] for t in sorted(common)) / total
            if alias_total:
                coverage = max(coverage, sum(idf[t] for t in sorted(common & aliases)) / alias_total)
            compact_heading = re.sub(r"\W+", "", doc.heading.casefold())
            exact = len(compact_heading) >= 3 and compact_heading in compact_query
            if exact:
                score *= self.policy.exact_heading_boost
            if re.search(r"什么是|是什么|定义|what is|define", focused) and re.search(r"什么是|定义|what is|definition", doc.heading, re.I):
                score *= 1.5
            score *= .4 + .6 * coverage
            ranked.append(EvidenceCandidate(KnowledgeChunk(f"kb:{doc.id}", doc.content,
                f"{doc.source_name} · {doc.heading}" if doc.heading else doc.source_name, round(score, 6), score_type="bm25f"),
                doc.source_id, doc.category, coverage, exact))
        return sorted(ranked, key=lambda c: (-c.chunk.score, c.chunk.id))[:limit]

    def search(self, *, module: str, query: str, top_k: int = 3) -> list[KnowledgeChunk]:
        if top_k <= 0:
            return []
        candidates = [c for c in self.candidates(module=module, query=query, limit=max(32, top_k * 8))
                      if (c.coverage >= self.policy.min_coverage or c.heading_exact) and c.chunk.score >= self.policy.min_score]
        if not candidates:
            return []
        cutoff = candidates[0].chunk.score * self.policy.relative_score
        counts, hits = Counter(), []
        for candidate in candidates:
            if candidate.chunk.score < cutoff or counts[candidate.source_id] >= self.policy.max_per_source:
                continue
            hits.append(candidate.chunk)
            counts[candidate.source_id] += 1
            if len(hits) == top_k:
                break
        return hits
