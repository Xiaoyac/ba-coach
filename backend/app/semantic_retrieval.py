"""Opt-in local hybrid retrieval and bilingual cross-encoder evidence reranking.

No business database, evaluation judgments, paid API, or generation model is
used here. Optional disk score caching is for explicit offline experiments only.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from time import perf_counter

from .retrieval import KnowledgeChunk, rank_candidates
from .knowledge_store import KNOWLEDGE_CATEGORY_MODULES
from .retrieval_enhanced import EnhancedKnowledgeRanker, focus_query
from .vector_retrieval import HybridPolicy, LocalEmbedder, LocalVectorKnowledgeBase, reciprocal_rank_fusion

ROOT = Path(__file__).resolve().parents[1] / ".rag-local"
VERSION = "hybrid-bge-window-v1"


@dataclass(frozen=True)
class SemanticPolicy:
    candidate_limit: int = 32
    vector_candidates: int = 12
    min_logit: float = 0.0
    max_logit_gap: float = 3.0
    max_per_source: int = 2

    def __post_init__(self):
        if self.candidate_limit < 1 or self.vector_candidates < 0 or self.max_per_source < 1:
            raise ValueError("Invalid candidate budget")
        if not math.isfinite(self.min_logit) or not math.isfinite(self.max_logit_gap) or self.max_logit_gap < 0:
            raise ValueError("Invalid reranking threshold")


class WindowCrossEncoder:
    def __init__(self, cache_dir: Path = ROOT / "rerank-models", model="BAAI/bge-reranker-base"):
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        from tokenizers import Tokenizer
        self.encoder = TextCrossEncoder(model, cache_dir=str(cache_dir), threads=2)
        self.tokenizer = Tokenizer.from_str(self.encoder.model.tokenizer.to_str())
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        digest = hashlib.sha256()
        for path in sorted(p for p in Path(self.encoder.model._model_dir).rglob("*") if p.is_file()):
            digest.update(str(path.relative_to(self.encoder.model._model_dir)).encode())
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        self.identity = model + ":" + digest.hexdigest() + ":max-window-64overlap-v1"

    def scores(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        tokens = self.tokenizer.encode(query, add_special_tokens=False)
        if len(tokens.ids) > 160:
            raise ValueError("Reranker query exceeds its explicit 160-token budget")
        size = 512 - len(tokens.ids) - 12
        pairs, owners = [], []
        for index, document in enumerate(documents):
            encoded = self.tokenizer.encode(document, add_special_tokens=False)
            offsets = encoded.offsets
            if not offsets:
                raise ValueError("Cannot rerank an empty document")
            for start in range(0, len(offsets), size - 64):
                end = min(start + size, len(offsets))
                pairs.append((query, document[offsets[start][0]:offsets[end - 1][1]]))
                owners.append(index)
                if end == len(offsets):
                    break
        raw = list(self.encoder.rerank_pairs(pairs, batch_size=4))
        if len(raw) != len(pairs) or any(not math.isfinite(v) for v in raw):
            raise ValueError("Invalid cross-encoder scores")
        result = [-math.inf] * len(documents)
        for index, score in zip(owners, raw):
            result[index] = max(result[index], score)
        return result


class HybridKnowledgeRanker:
    def __init__(self, corpus, *, policy: SemanticPolicy | None = None, use_vectors=True,
                 cross_encoder=None, vector_store=None, score_cache: Path | None = None):
        self.corpus = tuple(corpus)
        self.lexical = EnhancedKnowledgeRanker(self.corpus)
        self.policy = policy or SemanticPolicy()
        self.cross_encoder = cross_encoder or WindowCrossEncoder()
        self.vector = vector_store
        if use_vectors and self.vector is None:
            self.vector = LocalVectorKnowledgeBase(self.corpus, LocalEmbedder(ROOT / "models"),
                ROOT / "qdrant", mode="vector", policy=HybridPolicy(vector_min_score=.3, max_per_source=8))
            try:
                if not self.vector.ready():
                    raise RuntimeError("Explicitly build the matching vector index before evaluation")
            except Exception:
                self.vector.close()
                raise
        self.score_cache = score_cache
        if score_cache is not None:
            score_cache.mkdir(parents=True, exist_ok=True)
        self.last_trace = {}

    def candidate_chunks(self, *, module, query):
        focused = focus_query(query)
        bm25 = self.lexical.candidates(module=module, query=focused, limit=self.policy.candidate_limit)
        legacy = rank_candidates(self.corpus, module=module, query=focused)[:self.policy.candidate_limit]
        dense = self.vector.search_sync(module=module, query=focused,
            top_k=self.policy.vector_candidates, mode="vector") if self.vector and self.policy.vector_candidates else []
        rankings = [[c.chunk.id for c in bm25], [c.chunk.id for c in legacy]]
        if dense:
            rankings.append([c.id for c in dense])
        fused = reciprocal_rank_fusion(*rankings)
        # RRF is ordering, never a relevance threshold or a probability.
        documents = []
        for identifier, _ in fused:
            doc = self.lexical.by_id.get(identifier)
            if doc is None:
                raise ValueError("Vector index contains an unknown knowledge ID")
            if module in KNOWLEDGE_CATEGORY_MODULES.get(doc.category, ()):
                documents.append(doc)
            if len(documents) == self.policy.candidate_limit:
                break
        return documents

    def scored_candidates(self, *, module, query):
        started = perf_counter()
        docs = self.candidate_chunks(module=module, query=query)
        if not docs:
            self.last_trace = {"candidate_count": 0, "cache_hit": False, "duration_ms": 0}
            return []
        # Keep the actual user question for semantic judgment, including negation.
        # Historical user messages follow it and serve only as reference context.
        payload = {"version": VERSION, "model": self.cross_encoder.identity,
            "module": module, "query": query, "documents": [(d.id, d.heading, d.content) for d in docs]}
        fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = self.score_cache / (fingerprint + ".json") if self.score_cache else None
        cache_hit = bool(path and path.exists())
        if cache_hit:
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("Unreadable offline score cache; explicitly rebuild it") from exc
            if stored["fingerprint"] != fingerprint or len(stored["scores"]) != len(docs):
                raise ValueError("Stale score cache")
            scores, inference_ms = stored["scores"], stored["inference_ms"]
        else:
            inference_started = perf_counter()
            scores = self.cross_encoder.scores(query, [d.content for d in docs])
            inference_ms = (perf_counter() - inference_started) * 1000
            if len(scores) != len(docs) or any(not math.isfinite(v) for v in scores):
                raise ValueError("Invalid reranker output length/value")
            if path:
                fd, temporary = tempfile.mkstemp(prefix=".score-", dir=path.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        json.dump({"fingerprint": fingerprint, "scores": scores,
                            "inference_ms": inference_ms}, stream)
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
        if (len(scores) != len(docs) or any(not math.isfinite(v) for v in scores)
                or not math.isfinite(inference_ms) or inference_ms < 0):
            raise ValueError("Nonfinite reranker output")
        self.last_trace = {"candidate_count": len(docs), "cache_hit": cache_hit,
            "inference_ms_original": inference_ms, "duration_ms": (perf_counter() - started) * 1000,
            "model": self.cross_encoder.identity, "fingerprint": fingerprint}
        return sorted(zip(docs, scores), key=lambda pair: (-pair[1], pair[0].id))

    def search(self, *, module: str, query: str, top_k: int = 3):
        if not query.strip() or top_k <= 0:
            return []
        ranked = self.scored_candidates(module=module, query=query)
        if not ranked:
            return []
        cutoff = max(self.policy.min_logit, ranked[0][1] - self.policy.max_logit_gap)
        counts, hits = Counter(), []
        for doc, score in ranked:
            if score < cutoff or counts[doc.source_id] >= self.policy.max_per_source:
                continue
            hits.append(KnowledgeChunk(f"kb:{doc.id}", doc.content, f"{doc.source_name} · {doc.heading}", score, score_type="cross_encoder"))
            counts[doc.source_id] += 1
            if len(hits) == top_k:
                break
        return hits

    def close(self):
        if self.vector:
            self.vector.close()
