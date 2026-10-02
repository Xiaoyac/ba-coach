"""Dedicated embedding APIs; no chat-model emulation or silent local fallback.

Runs in the hybrid index worker, never on the request event loop. Persisted
vectors include provider/model/dimension identity; queries remain ephemeral.
"""
from __future__ import annotations

import hashlib
import json
import math

import httpx


class RemoteEmbedder:
    def __init__(self, settings):
        if not settings.knowledge_embedding_url or not settings.knowledge_embedding_api_key:
            raise ValueError("Dedicated embedding URL and key are required")
        self.settings = settings
        self.dimension = settings.knowledge_embedding_dimensions
        identity = {
            "backend": settings.knowledge_embedding_backend,
            "url": settings.knowledge_embedding_url,
            "model": settings.knowledge_embedding_model,
            "dimensions": self.dimension,
            "encoding": "whole-passage-query-document-v1",
        }
        self.identity = "remote-v1:" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        self.last_usage = {}

    def _encode(self, texts, *, query):
        settings = self.settings
        result = []
        tokens, reported_batches = 0, 0
        self.last_usage = {}
        with httpx.Client(timeout=settings.knowledge_embedding_timeout_seconds) as client:
            for start in range(0, len(texts), settings.knowledge_embedding_batch_size):
                batch = texts[start:start + settings.knowledge_embedding_batch_size]
                if settings.knowledge_embedding_backend == "dashscope":
                    body = {"model": settings.knowledge_embedding_model, "input": {"texts": batch},
                            "parameters": {"dimension": self.dimension, "output_type": "dense",
                                           "text_type": "query" if query else "document"}}
                else:
                    body = {"model": settings.knowledge_embedding_model, "input": batch,
                            "dimensions": self.dimension, "encoding_format": "float"}
                response = client.post(settings.knowledge_embedding_url,
                    headers={"Authorization": f"Bearer {settings.knowledge_embedding_api_key}"}, json=body)
                response.raise_for_status()
                data = response.json()
                if data.get("code"):
                    raise ValueError("Embedding provider returned an error")
                if settings.knowledge_embedding_backend == "dashscope":
                    rows, index_key = data["output"]["embeddings"], "text_index"
                else:
                    rows, index_key = data["data"], "index"
                indices = [row[index_key] for row in rows]
                if any(type(i) is not int for i in indices) or sorted(indices) != list(range(len(batch))):
                    raise ValueError("Embedding response indices are incomplete or duplicated")
                for row in sorted(rows, key=lambda row: row[index_key]):
                    vector = row["embedding"]
                    if len(vector) != self.dimension or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
                        raise ValueError("Invalid embedding shape or values")
                    norm = math.sqrt(sum(v * v for v in vector))
                    if not math.isfinite(norm) or norm <= 0:
                        raise ValueError("Invalid embedding norm")
                    result.append([v / norm for v in vector])
                usage = data.get("usage") or {}
                count = usage.get("total_tokens", usage.get("prompt_tokens"))
                if type(count) is int and count >= 0:
                    tokens += count
                    reported_batches += 1
        self.last_usage = {"reported_tokens": tokens if reported_batches else None,
                           "usage_complete": reported_batches == math.ceil(len(texts) / settings.knowledge_embedding_batch_size)}
        return result

    def passages(self, texts):
        return self._encode(texts, query=False)

    def query(self, text):
        return self._encode([text], query=True)[0]
