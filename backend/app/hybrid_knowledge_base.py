"""Database-backed dense/BM25 retrieval, inspired by AstrBot's retrieval stages.

Own implementation: the database remains authoritative. Versioned, derived
vectors are local files; user queries and conversation state are never persisted
in this index. No catalog, chat-model selector or mediator is used here.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
from time import perf_counter
from concurrent.futures import ThreadPoolExecutor

from .config import get_settings
from .knowledge_store import KNOWLEDGE_CATEGORY_MODULES
from .retrieval import DatabaseKnowledgeBase, KnowledgeChunk
from .retrieval_cache import SearchResult
from .retrieval_enhanced import focus_query
from .vector_retrieval import LocalEmbedder

logger = logging.getLogger(__name__)
VERSION = "astrbot-style-hybrid-v2"
VECTOR_FORMAT = "astrbot-style-hybrid-v1"  # Ranking changes do not re-embed unchanged passages.


def normalized(scores):
    if not scores:
        return {}
    low, high = min(scores.values()), max(scores.values())
    return {key: (value - low) / (high - low) if high > low else 1.0
            for key, value in scores.items()}


def fuse(dense, sparse, groups, *, weight=0.9):
    """Dense min/max globally; BM25 per KB category; RRF only breaks ties."""
    dense_scores = normalized(dict(dense))
    per_group = defaultdict(dict)
    for key, score in sparse:
        per_group[groups[key]][key] = score
    sparse_scores = {key: score for group in per_group.values()
                     for key, score in normalized(group).items()}
    dense_ranks = {key: rank for rank, (key, _) in enumerate(dense, 1)}
    # Sparse ranks are local to each KB, as in AstrBot SparseResult.rank.
    group_ranks = defaultdict(int)
    sparse_ranks = {}
    for key, _ in sparse:
        group_ranks[groups[key]] += 1
        sparse_ranks[key] = group_ranks[groups[key]]
    ranks = [dense_ranks, sparse_ranks]
    scores = {key: weight * dense_scores.get(key, 0) + (1 - weight) * sparse_scores.get(key, 0)
              for key in dense_scores.keys() | sparse_scores.keys()}
    return sorted(scores.items(), key=lambda item: (
        -item[1], -sum(1 / (60 + rank[item[0]]) for rank in ranks if item[0] in rank),
        dense_ranks.get(item[0], float("inf")), sparse_ranks.get(item[0], float("inf")), item[0]))


def tokenize(text):
    import jieba
    from .retrieval import _NON_EVIDENCE_TERMS
    stopwords = _NON_EVIDENCE_TERMS | set("我 你 他 她 它 的 地 得 了 着 过 是 在 和 与 或 而 也 就 都 很 有 没 吗 呢 啊 吧 把 被 让 给 对 这 那 个 一 不 但 只 想 要 想要 不想 只想 可以 i me my it is to a an of in on at be do does not that so as but if then there they them we us".split())
    return [word for word in jieba.cut(text.casefold())
            if word not in stopwords and re.search(r"\w", word)]


@dataclass
class Snapshot:
    documents: tuple
    vectors: object
    scopes: dict
    identity: str


class HybridDatabaseKnowledgeBase(DatabaseKnowledgeBase):
    def __init__(self, *args, embedder=None, storage=None, **kwargs):
        super().__init__(*args, ranking_mode="p0", **kwargs)
        self.ranking_mode = "astrbot_hybrid"
        self.settings = get_settings()
        self.storage = Path(storage or self.settings.knowledge_hybrid_storage)
        self._embedder = embedder
        self._snapshot = None
        self._build_task = None
        self._build_index = None
        # Serialize ONNX inference and index writes across request threads.
        self._worker_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kb-hybrid")

    def _cache_namespace(self):
        # Provider URLs may contain deployment identities. Hash, never log keys.
        config = {name: getattr(self.settings, name) for name in (
            "knowledge_embedding_backend", "knowledge_embedding_url", "knowledge_embedding_dimensions",
            "knowledge_embedding_model", "knowledge_hybrid_dense_weight",
            "knowledge_hybrid_dense_candidates", "knowledge_hybrid_sparse_candidates",
            "knowledge_hybrid_candidates", "knowledge_rerank_model", "knowledge_rerank_url",
            "knowledge_rerank_backend", "knowledge_rerank_min_score", "knowledge_hybrid_require_rerank")}
        return {**super()._cache_namespace(), "hybrid_version": VERSION,
                "config": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()}

    def _ensure_embedder(self):
        if self._embedder is None:
            if self.settings.knowledge_embedding_backend == "local":
                self._embedder = LocalEmbedder(self.storage / "models", self.settings.knowledge_embedding_model,
                                              local_files_only=True)
            else:
                from .knowledge_embedding import RemoteEmbedder
                self._embedder = RemoteEmbedder(self.settings)
        return self._embedder

    def _validate_quality_configuration(self):
        if self.settings.knowledge_hybrid_require_rerank and not all((
                self.settings.knowledge_rerank_model, self.settings.knowledge_rerank_url,
                self.settings.knowledge_rerank_api_key)):
            raise ValueError("Quality profile requires a dedicated reranker")

    def _vector(self, value, dimension):
        import numpy as np
        vector = np.asarray(value, dtype="float32")
        if vector.shape != (dimension,) or not np.isfinite(vector).all():
            raise ValueError("Invalid embedding shape or values")
        norm = float(np.linalg.norm(vector))
        if norm <= 0:
            raise ValueError("Zero embedding")
        return vector / norm

    def _build_sync(self, documents):
        import faiss
        import numpy as np
        from .knowledge_sparse import SparseIndex
        with self._worker_lock:
            embedder = self._ensure_embedder()
            identity = hashlib.sha256((VECTOR_FORMAT + embedder.identity).encode()).hexdigest()
            folder = self.storage / "vectors" / identity
            folder.mkdir(parents=True, exist_ok=True)
            vectors, pending = [], []
            for position, doc in enumerate(documents):
                text = doc.heading + "\n" + doc.content
                filename = folder / (hashlib.sha256(text.encode()).hexdigest() + ".npy")
                try:
                    vector = self._vector(np.load(filename, allow_pickle=False), embedder.dimension)
                except (OSError, ValueError, EOFError):
                    vector = None
                    pending.append((position, text, filename))
                vectors.append(vector)
            for start in range(0, len(pending), 16):
                batch = pending[start:start + 16]
                embedded = embedder.passages([item[1] for item in batch])
                if len(embedded) != len(batch):
                    raise ValueError("Embedding count mismatch")
                for (position, _, filename), value in zip(batch, embedded):
                    vector = self._vector(value, embedder.dimension)
                    vectors[position] = vector
                    # Atomic publication allows workers/releases to share derived
                    # vectors. Partial/crashed builds can never be loaded.
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as stream:
                            temporary = stream.name
                            np.save(stream, vector, allow_pickle=False)
                        os.replace(temporary, filename)
                    finally:
                        if temporary and os.path.exists(temporary):
                            os.unlink(temporary)
            matrix = np.asarray(vectors, dtype="float32").reshape(len(documents), embedder.dimension)
            scopes = {}
            for module in {m for modules in KNOWLEDGE_CATEGORY_MODULES.values() for m in modules}:
                positions = [i for i, d in enumerate(documents)
                             if module in KNOWLEDGE_CATEGORY_MODULES.get(d.category, ())]
                if not positions:
                    continue
                categories = {}
                for category in sorted({documents[i].category for i in positions}):
                    members = [i for i in positions if documents[i].category == category]
                    dense = faiss.IndexFlatIP(embedder.dimension)
                    dense.add(matrix[members])
                    tokens = [tokenize(documents[i].heading + "\n" + documents[i].content) or ["__empty__"]
                              for i in members]
                    categories[category] = (members, dense, SparseIndex(members, tokens))
                scopes[module] = categories
            return Snapshot(documents, matrix, scopes, identity)

    def _request_build(self, documents):
        if (self._build_task is not None and self._build_task.done()
                and not self._build_task.cancelled() and self._build_index is documents
                and self._build_task.exception() is None):
            return self._build_task
        if self._build_task is None or self._build_task.done():
            if self._build_task is not None and not self._build_task.cancelled():
                # Observe background exceptions, then allow a later retry.
                self._build_task.exception()
            self._build_index = documents
            self._build_task = asyncio.get_running_loop().run_in_executor(self._executor, self._build_sync, documents)
        return self._build_task

    async def warmup(self):
        self._validate_quality_configuration()
        documents = await self._load_index(force=True)
        snapshot = await self._request_build(documents)
        if snapshot.documents is documents:
            self._snapshot = snapshot
        return len(documents)

    def _search_sync(self, snapshot, *, module, query, top_k, trace=None):
        import numpy as np
        with self._worker_lock:
            scope = snapshot.scopes.get(module)
            if scope is None:
                return []
            focused = focus_query(query)
            embedder = self._ensure_embedder()
            started = perf_counter()
            if trace is not None:
                trace["embedding_api_calls"] = int(self.settings.knowledge_embedding_backend != "local")
            query_vector = self._vector(embedder.query(focused), snapshot.vectors.shape[1])
            if trace is not None:
                trace["embedding_duration_ms"] = round((perf_counter() - started) * 1000, 3)
            retrieval_started = perf_counter()
            # No bilingual expansion or coverage gate: BM25 and dense see the
            # same focused query. Rejection focusing remains BA-specific policy.
            terms = tokenize(focused)
            dense, sparse, counts = [], [], {}
            for category, (members, dense_index, ranker) in scope.items():
                scores, ids = dense_index.search(np.asarray([query_vector], dtype="float32"),
                    min(self.settings.knowledge_hybrid_dense_candidates, len(members)))
                dense_part = [(members[int(i)], float(score)) for i, score in zip(ids[0], scores[0]) if i >= 0]
                sparse_part = ranker.search(terms, self.settings.knowledge_hybrid_sparse_candidates)
                dense.extend(dense_part)
                sparse.extend(sparse_part)
                counts[category] = {"dense": len(dense_part), "sparse": len(sparse_part)}
            dense.sort(key=lambda item: (-item[1], item[0]))
            groups = {i: d.category for i, d in enumerate(snapshot.documents)}
            fused = fuse(dense, sparse, groups, weight=self.settings.knowledge_hybrid_dense_weight)
            seen, hits = set(), []
            for i, score in fused:
                doc = snapshot.documents[i]
                if doc.content in seen:
                    continue
                seen.add(doc.content)
                hits.append(KnowledgeChunk(f"kb:{doc.id}", doc.content,
                    f"{doc.source_name} · {doc.heading}" if doc.heading else doc.source_name,
                    round(score, 6), "relative_score_fusion"))
                if len(hits) >= top_k:
                    break
            if trace is not None:
                trace.update(candidates_by_category=counts,
                    dense_ids=[f"kb:{snapshot.documents[i].id}" for i, _ in dense],
                    sparse_ids=[f"kb:{snapshot.documents[i].id}" for i, _ in sparse],
                    fused_ids=[hit.id for hit in hits],
                    recall_fusion_duration_ms=round((perf_counter() - retrieval_started) * 1000, 3),
                    embedding_usage=dict(getattr(embedder, "last_usage", {})))
            return hits

    async def _search_index(self, index, *, module, query, top_k):
        trace = {"embedding_api_calls": 0, "rerank_api_calls": 0}
        try:
            self._validate_quality_configuration()
            if self._snapshot is None or self._snapshot.documents is not index:
                task = self._request_build(index)
                # Never make a user wait through a model download/full rebuild.
                # Shield preserves the background build when this turn times out.
                try:
                    snapshot = await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
                except asyncio.TimeoutError:
                    return SearchResult(status="withheld_index_building", cacheable=False)
                if snapshot.documents is not index:
                    return SearchResult(status="withheld_index_building", cacheable=False)
                self._snapshot = snapshot
            from functools import partial
            hits = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._executor,
                partial(self._search_sync, self._snapshot, module=module, query=query,
                        top_k=max(top_k, self.settings.knowledge_hybrid_candidates), trace=trace)),
                timeout=self.settings.knowledge_hybrid_timeout_seconds)
            rerank_status = "disabled"
            if self.settings.knowledge_rerank_model and hits:
                trace["rerank_api_calls"] = 1
                rerank_started = perf_counter()
                ranked = await self._rerank(focus_query(query), hits, trace=trace)
                trace["rerank_duration_ms"] = round((perf_counter() - rerank_started) * 1000, 3)
                rerank_status = "failed_retained_fusion" if ranked is hits else "completed"
                if ranked is hits and self.settings.knowledge_hybrid_require_rerank:
                    return SearchResult(status="withheld_rerank_error", cacheable=False,
                                        model_calls=sum(trace[k] for k in ("embedding_api_calls", "rerank_api_calls")),
                                        diagnostics={**trace, "rerank": "failed", "profile": "quality"})
                hits = ranked
            return SearchResult(tuple(hits[:top_k]), status="hybrid_completed",
                model_calls=sum(trace[k] for k in ("embedding_api_calls", "rerank_api_calls")),
                cacheable=rerank_status != "failed_retained_fusion", diagnostics={
                **trace, "version": VERSION, "final_limit": top_k,
                "embedding": self.settings.knowledge_embedding_model, "embedding_location": self.settings.knowledge_embedding_backend,
                "index_identity": self._snapshot.identity, "index_chunks": len(index),
                "dense_weight": self.settings.knowledge_hybrid_dense_weight,
                "fusion": "normalized_scores_rrf_tiebreak", "sparse_backend": "sqlite_fts5_bm25", "rerank": rerank_status,
                "catalog_calls": 0, "mediator_calls": 0})
        except asyncio.TimeoutError:
            return SearchResult(status="withheld_hybrid_timeout", cacheable=False, diagnostics=dict(trace),
                model_calls=sum(trace[k] for k in ("embedding_api_calls", "rerank_api_calls")))
        except Exception as exc:
            logger.warning("hybrid retrieval unavailable: %s", type(exc).__name__)
            return SearchResult(status="withheld_hybrid_error", cacheable=False,
                diagnostics={**trace, "error_type": type(exc).__name__},
                model_calls=sum(trace[k] for k in ("embedding_api_calls", "rerank_api_calls")))

    async def _rerank(self, query, hits, *, trace=None):
        """Optional dedicated rerank API. Failure retains the fused candidates."""
        import httpx
        from dataclasses import replace
        try:
            async with httpx.AsyncClient(timeout=self.settings.knowledge_rerank_timeout_seconds) as client:
                payload = {"model": self.settings.knowledge_rerank_model, "query": query,
                           "documents": [hit.text for hit in hits], "top_n": len(hits)}
                if self.settings.knowledge_rerank_backend == "dashscope":
                    payload = {"model": payload["model"], "input": {"query": query,
                        "documents": payload["documents"]}, "parameters": {"top_n": len(hits)}}
                response = await asyncio.wait_for(client.post(self.settings.knowledge_rerank_url,
                    headers={"Authorization": f"Bearer {self.settings.knowledge_rerank_api_key or ''}"},
                    json=payload), timeout=self.settings.knowledge_rerank_timeout_seconds)
                response.raise_for_status()
                data = response.json()
                if data.get("code"):
                    raise ValueError("Rerank provider returned an error")
                rows = data["output"]["results"] if self.settings.knowledge_rerank_backend == "dashscope" else data["results"]
                if trace is not None:
                    usage = data.get("usage") or {}
                    trace["rerank_usage"] = {key: value for key, value in usage.items()
                        if key in {"total_tokens", "input_tokens", "output_tokens", "prompt_tokens"}
                        and type(value) is int and value >= 0}
                    trace["rerank_model"] = self.settings.knowledge_rerank_model
                import math
                if (any(type(row["index"]) is not int for row in rows)
                        or sorted(row["index"] for row in rows) != list(range(len(hits)))):
                    raise ValueError("Incomplete or duplicate rerank result")
                if not all(math.isfinite(float(row["relevance_score"])) for row in rows):
                    raise ValueError("Invalid rerank score")
                cutoff = self.settings.knowledge_rerank_min_score
                ranked = [replace(hits[row["index"]], score=float(row["relevance_score"]), score_type="cross_encoder")
                          for row in sorted(rows, key=lambda row: -float(row["relevance_score"]))
                          if cutoff is None or float(row["relevance_score"]) >= cutoff]
                if trace is not None:
                    trace.update(rerank_min_score=cutoff, reranked_ids=[hit.id for hit in ranked])
                return ranked
        except Exception as exc:
            logger.warning("hybrid rerank failed: %s", type(exc).__name__)
            return hits
