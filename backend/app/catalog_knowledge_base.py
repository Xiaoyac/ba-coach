"""Opt-in catalog runtime, selected with KNOWLEDGE_RETRIEVAL_MODE=catalog.

Also supports explicit GraphContext injection. The downstream knowledge
mediator remains mandatory, including on exact retrieval cache hits. No local
evaluation corpus, score caches, SSH relay or labels are used at runtime.
"""
import asyncio
import json
import logging
import math
from collections import Counter
from time import perf_counter

from .db import get_sessionmaker
from .retrieval import DatabaseKnowledgeBase
from .retrieval_cache import SearchResult
from .semantic_catalog import SemanticCatalog, PLAN_PROMPT, JUDGE_PROMPT, VERSION

logger = logging.getLogger(__name__)


class CatalogDatabaseKnowledgeBase(DatabaseKnowledgeBase):
    def __init__(self, provider, sessionmaker_provider=get_sessionmaker, *, timeout_seconds=12):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Catalog timeout must be finite and positive")
        super().__init__(sessionmaker_provider, ranking_mode="p0")
        self.ranking_mode = "catalog"
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self._catalog = None
        self._catalog_index = None
        self._public_cache_counts = Counter()

    def invalidate(self, reason="manual"):
        super().invalidate(reason)
        self._catalog = None
        self._catalog_index = None

    def cache_monitoring_stats(self):
        stats = super().cache_monitoring_stats()
        hits = self._public_cache_counts["hits"]
        requests = hits + self._public_cache_counts["misses"]
        stats["public_preparation"] = {
            "hits": hits, "requests": requests, "hit_rate": hits / requests if requests else None,
            "entries": len(self._catalog._public_scopes) if self._catalog else 0,
            "saved_model_calls": 0,
        }
        return stats

    async def _ask(self, prompt, payload):
        serialized = json.dumps(payload, ensure_ascii=False)
        if len(serialized) > 100_000:
            raise ValueError("Catalog payload budget exceeded")
        response = await self.provider.route_detailed(system=prompt, user=serialized, max_tokens=256)
        if response.finish_reason != "stop":
            raise ValueError("Incomplete catalog response")
        return response.text

    def _cache_namespace(self):
        # Explicit allowlist: never serialize provider objects, API keys or
        # credentials. Changing provider/model/prompt invalidates exact reuse.
        settings = getattr(self.provider, "_settings", None)
        configured = {key: getattr(settings, key, None) for key in (
            "deepseek_router_model", "deepseek_base_url", "doubao_router_model",
            "doubao_base_url", "claude_router_model", "router_max_tokens")}
        return {**super()._cache_namespace(), "catalog_version": VERSION,
                "plan_prompt": PLAN_PROMPT, "judge_prompt": JUDGE_PROMPT,
                "provider_type": type(self.provider).__qualname__,
                "provider_instance": id(self.provider),
                "provider_name": getattr(self.provider, "name", None),
                "model": getattr(self.provider, "model", None), "configured": configured,
                "max_tokens": 256, "timeout_seconds": self.timeout_seconds}

    async def _search_index(self, index, *, module, query, top_k):
        started = perf_counter()
        trace = {"module":module, "method":"catalog", "status":"error", "model_calls":0}

        async def run():
            if self._catalog is None or self._catalog_index is not index:
                self._catalog = SemanticCatalog(index, public_cache_counts=self._public_cache_counts)
                self._catalog_index = index
            catalog = self._catalog
            fast = catalog.fast_path(module=module,query=query,top_k=top_k)
            if fast is not None:
                trace["status"] = "explicit_title_fast_path"
                return fast
            payload = catalog.plan_payload(module=module,query=query)
            if not payload["sections"]:
                trace["status"] = "no_scoped_sections"
                return []
            trace["model_calls"] += 1
            raw = await self._ask(PLAN_PROMPT,payload)
            docs = catalog.candidates(module=module,query=query,plan_raw=raw)
            trace["candidate_count"] = len(docs)
            if not docs:
                trace["status"] = "no_applicable_section"
                return []
            trace["model_calls"] += 1
            raw = await self._ask(JUDGE_PROMPT,catalog.judge_payload(module=module,query=query,documents=docs,top_k=top_k))
            hits = catalog.results(raw,docs,top_k=top_k)
            trace["status"] = "completed"
            return hits

        try:
            hits = await asyncio.wait_for(run(),timeout=self.timeout_seconds)
            trace["returned"] = len(hits)
            return SearchResult(tuple(hits), status=trace["status"], model_calls=trace["model_calls"])
        except Exception as exc:
            # No provider text, query, user identity, secrets or raw candidates
            # enter diagnostics. Invalid responses NEVER trigger a raw fallback.
            trace.update({"status":"withheld_on_error", "error_type":type(exc).__name__, "returned":0})
            return SearchResult(status="withheld_on_error", cacheable=False, model_calls=trace["model_calls"])
        finally:
            trace["duration_ms"] = round((perf_counter()-started)*1000,3)
            logger.info("catalog_retrieval %s",json.dumps(trace))
