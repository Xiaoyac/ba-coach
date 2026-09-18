"""Local RAG experiment. No business database access, migrations or LLM calls.

Qdrant is a derived index of a frozen file corpus. This module deliberately is
not wired into the production singleton until lifecycle/fallback work is ready.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Protocol

from .knowledge_store import KNOWLEDGE_CATEGORY_MODULES
from .retrieval import KnowledgeChunk, RetrievalPolicy, rank_candidates, select_candidates

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class Embedder(Protocol):
    identity: str
    dimension: int

    def passages(self, texts: list[str]) -> list[list[float]]: ...
    def query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    """CPU ONNX inference. Mean-pool token windows, never silently drop tails.

    Model weights are downloaded on first use. Knowledge/query text stays local.
    Window pooling is an experiment baseline, not a substitute for semantic
    chunking; a long chunk can dilute a short relevant passage.
    """

    def __init__(self, cache_dir: Path, model: str = DEFAULT_MODEL):
        from fastembed import TextEmbedding
        from tokenizers import Tokenizer

        self.model = TextEmbedding(model, cache_dir=str(cache_dir), threads=2)
        description = next(m for m in TextEmbedding.list_supported_models() if m["model"] == model)
        self.dimension = description["dim"]
        tokenizer = self.model.model.tokenizer
        self.tokenizer = Tokenizer.from_str(tokenizer.to_str())
        self.window_size = min((tokenizer.truncation or {}).get("max_length", 128), 128) - 8
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        # Include downloaded asset hashes: replacing weights invalidates the index.
        assets = Path(self.model.model._model_dir)
        digest = hashlib.sha256()
        for path in sorted(p for p in assets.rglob("*") if p.is_file()):
            digest.update(str(path.relative_to(assets)).encode())
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        self.identity = f"fastembed-0.7.4:{model}:{digest.hexdigest()}:mean-window-v1:{self.window_size}"

    def _windows(self, text: str) -> list[str]:
        encoded = self.tokenizer.encode(text, add_special_tokens=False)
        offsets = encoded.offsets
        if not offsets:
            raise ValueError("Cannot embed empty text")
        windows = []
        for start in range(0, len(offsets), self.window_size):
            end = min(start + self.window_size, len(offsets))
            windows.append(text[offsets[start][0]:offsets[end - 1][1]])
        return windows

    def _encode(self, texts: list[str], *, query: bool) -> list[list[float]]:
        import numpy as np

        groups = [self._windows(text) for text in texts]
        flat = [window for group in groups for window in group]
        encode = self.model.query_embed if query else self.model.passage_embed
        vectors = list(encode(flat, batch_size=16))
        result, offset = [], 0
        for group in groups:
            vector = np.mean(vectors[offset:offset + len(group)], axis=0)
            norm = float(np.linalg.norm(vector))
            if not math.isfinite(norm) or norm <= 0:
                raise ValueError("Invalid embedding")
            result.append((vector / norm).tolist())
            offset += len(group)
        return result

    def passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, query=False)

    def query(self, text: str) -> list[float]:
        return self._encode([text], query=True)[0]


@dataclass(frozen=True)
class HybridPolicy:
    candidates: int = 20
    vector_min_score: float = 0.5  # Experimental cosine threshold, NOT probability.
    rrf_k: int = 60
    max_per_source: int = 2

    def __post_init__(self):
        if self.candidates < 1 or self.rrf_k < 1 or self.max_per_source < 1:
            raise ValueError("Counts must be positive")
        if not math.isfinite(self.vector_min_score) or not -1 <= self.vector_min_score <= 1:
            raise ValueError("Cosine threshold must be finite and in [-1, 1]")


def reciprocal_rank_fusion(*rankings: list[str], k: int = 60) -> list[tuple[str, float]]:
    if k < 1:
        raise ValueError("RRF k must be positive")
    scores: Counter[str] = Counter()
    for ranking in rankings:
        # Repeated entries in one channel must not vote twice.
        for rank, identifier in enumerate(dict.fromkeys(ranking), 1):
            scores[identifier] += 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class LocalVectorKnowledgeBase:
    """Compatible async search interface, plus explicit offline build/close.

    Local Qdrant has a single-process storage lock. Use one CLI process at a time.
    Content-addressed collections prevent mixing corpus/model versions; partial
    builds have no ready marker and cannot be searched.
    """

    def __init__(self, corpus, embedder: Embedder, path: Path | str,
                 *, mode: str = "hybrid", policy: HybridPolicy | None = None):
        from qdrant_client import QdrantClient

        if mode not in {"p0", "vector", "hybrid"}:
            raise ValueError("Unsupported retrieval mode")
        self.corpus, self.embedder, self.mode = tuple(corpus), embedder, mode
        self.policy = policy or HybridPolicy()
        self.by_id = {f"kb:{d.id}": d for d in corpus}
        if len(self.by_id) != len(self.corpus) or not self.corpus or any(d.id <= 0 for d in self.corpus):
            raise ValueError("Corpus must be nonempty with unique positive IDs")
        self.client = QdrantClient(location=":memory:") if str(path) == ":memory:" else QdrantClient(path=str(path))
        manifest = {"embedding": embedder.identity, "dimension": embedder.dimension,
                    "documents": [(d.id, d.source_id, d.source_name, d.category, d.heading, d.content)
                                  for d in self.corpus]}
        self.fingerprint = hashlib.sha256(json.dumps(manifest, ensure_ascii=False,
                                                    sort_keys=True).encode()).hexdigest()
        self.collection = f"ba_local_{self.fingerprint}"

    def ready(self) -> bool:
        if not self.client.collection_exists(self.collection):
            return False
        marker = self.client.retrieve(self.collection, ids=[0])
        return bool(marker and marker[0].payload.get("fingerprint") == self.fingerprint
                    and self.client.count(self.collection, exact=True).count == len(self.corpus) + 1)

    def build(self) -> bool:
        from qdrant_client import models

        if self.ready():
            return False
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(self.collection, vectors_config=models.VectorParams(
                size=self.embedder.dimension, distance=models.Distance.COSINE))
        for start in range(0, len(self.corpus), 32):
            batch = self.corpus[start:start + 32]
            vectors = self.embedder.passages([d.content for d in batch])
            if len(vectors) != len(batch):
                raise ValueError("Embedding count mismatch")
            points = []
            for document, vector in zip(batch, vectors):
                self._validate_vector(vector)
                points.append(models.PointStruct(id=document.id, vector=vector,
                    payload={"category": document.category, "source_id": document.source_id,
                             "content_hash": hashlib.sha256(document.content.encode()).hexdigest()}))
            self.client.upsert(self.collection, points=points, wait=True)
        self.client.upsert(self.collection, points=[models.PointStruct(
            id=0, vector=[1.0] + [0.0] * (self.embedder.dimension - 1),
            payload={"fingerprint": self.fingerprint, "kind": "ready"})], wait=True)
        return True

    def _validate_vector(self, vector):
        if (len(vector) != self.embedder.dimension or not all(math.isfinite(v) for v in vector)
                or not any(v != 0 for v in vector)):
            raise ValueError("Embedding dimension/value mismatch")

    def search_sync(self, *, module: str, query: str, top_k: int = 3,
                    mode: str | None = None) -> list[KnowledgeChunk]:
        from qdrant_client import models

        mode = mode or self.mode
        if mode not in {"p0", "vector", "hybrid"}:
            raise ValueError("Unsupported retrieval mode")
        categories = [c for c, modules in KNOWLEDGE_CATEGORY_MODULES.items() if module in modules]
        if not categories or not query.strip() or top_k <= 0:
            return []
        lexical = []
        if mode != "vector":
            ranked = rank_candidates(self.corpus, module=module, query=query)
            lexical = select_candidates(ranked, top_k=top_k if mode == "p0" else self.policy.candidates,
                policy=RetrievalPolicy(max_per_source=self.policy.max_per_source))
        if mode == "p0":
            return lexical
        if not self.ready():
            raise RuntimeError("Matching local index missing/incomplete; run rag_local.py build")
        vector = self.embedder.query(query)
        self._validate_vector(vector)
        matches = self.client.query_points(self.collection, query=vector,
            query_filter=models.Filter(must=[models.FieldCondition(
                key="category", match=models.MatchAny(any=categories))]),
            limit=max(top_k, self.policy.candidates), score_threshold=self.policy.vector_min_score,
            with_payload=False).points
        dense = [(f"kb:{point.id}", point.score) for point in matches]
        ranking = dense if mode == "vector" else reciprocal_rank_fusion(
            [c.id for c in lexical], [identifier for identifier, _ in dense], k=self.policy.rrf_k)
        counts: Counter[int] = Counter()
        hits = []
        for identifier, score in ranking:
            document = self.by_id[identifier]
            if counts[document.source_id] >= self.policy.max_per_source:
                continue
            counts[document.source_id] += 1
            hits.append(KnowledgeChunk(identifier, document.content,
                f"{document.source_name} · {document.heading}", score, score_type="rrf"))
            if len(hits) >= top_k:
                break
        return hits

    async def search(self, *, module: str, query: str, top_k: int = 3):
        return await asyncio.to_thread(self.search_sync, module=module, query=query, top_k=top_k)

    def close(self):
        self.client.close()
