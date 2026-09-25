"""Synthetic, offline tests. Never read production credentials or databases."""
import asyncio
import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.catalog_knowledge_base import CatalogDatabaseKnowledgeBase
from app.knowledge_store import import_knowledge_source, knowledge_revision
from app.models import KnowledgeChunkRecord, KnowledgeSourceRecord
from app.providers.base import Completion, as_text
from app.retrieval import DatabaseKnowledgeBase, KnowledgeChunk, KnowledgeRevisionChanged, index_chunk
from app.retrieval_cache import ExactRetrievalCache, SearchResult

QUERY = "没劲的时候该怎么开始？"
SCOPE = ("test-subject", "test-session", "False")
DOCS = (index_chunk(id=901, source_id=81, source_name="synthetic", category="BA",
    heading="行为激活原理", content="先付诸行动，不必等待动力。RAW_CACHE_SENTINEL"),)


class CatalogProvider:
    model = "synthetic-model"
    name = "stub"

    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = None
        self._settings = SimpleNamespace(deepseek_router_model="synthetic-router")

    async def route_detailed(self, **kwargs):
        self.calls += 1
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.mode == "timeout":
            raise asyncio.TimeoutError
        if self.mode == "error":
            raise RuntimeError("PRIVATE_ERROR_MUST_NOT_APPEAR")
        if self.mode == "invalid_json":
            return Completion(text="not-json", model=self.model, finish_reason="stop")
        payload = json.loads(kwargs["user"])
        field, candidates = ("section_ids", payload["sections"]) if "sections" in payload else ("selected_ids", payload["candidates"])
        ids = [] if self.mode == "empty" else [candidates[0]["id"]]
        if self.mode == "invalid_evidence":
            ids = ["invented"]
        return Completion(text=json.dumps({field: ids}), model=self.model,
                          finish_reason="length" if self.mode == "truncated" else "stop")


def synthetic_kb(provider=None):
    kb = CatalogDatabaseKnowledgeBase(provider or CatalogProvider())
    async def load(**kwargs):
        return DOCS
    kb._load_index = load
    return kb


async def search(kb, **changes):
    return await kb.search_with_diagnostics(**{
        "module": "module_1", "query": QUERY, "top_k": 2,
        "cache_scope": SCOPE, **changes})


async def publish(factory, content, *, category="BA", name="cache-test"):
    async with factory() as db:
        result = await import_knowledge_source(db, name=name, category=category,
                                               markdown=content, updated_by="synthetic")
        await db.commit()
        return result


async def test_exact_hit_saves_two_calls_preserves_order_and_is_not_mutable(caplog):
    kb = synthetic_kb()
    first, cold = await search(kb)
    second, warm = await search(kb)
    assert first == second and len(first) == 1
    assert cold["cache"] == "miss" and cold["model_calls"] == 2
    assert warm["cache"] == "hit" and warm["model_calls"] == 0
    assert warm["saved_model_calls"] == 2 and kb.provider.calls == 2
    second.clear()
    assert (await search(kb))[0] == first
    keys = list(kb._result_cache._entries)
    assert len(keys[0]) == 64 and QUERY not in str(keys) and SCOPE[0] not in str(keys)
    for private in (QUERY, *SCOPE[:2], "RAW_CACHE_SENTINEL"):
        assert private not in caplog.text


@pytest.mark.parametrize("changes", [
    {"query": QUERY + "\n膝盖痛"}, {"query": QUERY + " "},
    {"module": "module_2"}, {"top_k": 1},
    {"cache_scope": ("another-subject", "test-session", "False")},
    {"cache_scope": ("test-subject", "another-session", "False")},
    {"cache_scope": ("test-subject", "test-session", "True")},
])
async def test_full_context_and_scope_are_exact(changes):
    kb = synthetic_kb()
    await search(kb)
    _, metric = await search(kb, **changes)
    assert metric["cache"] == "miss" and kb.provider.calls == 4


@pytest.mark.parametrize("change", ["model", "router", "provider", "prompt", "policy"])
async def test_configuration_changes_never_reuse_old_results(change, monkeypatch):
    kb = synthetic_kb()
    original = kb.provider
    await search(kb)
    if change == "model":
        kb.provider.model = "new-model"
    elif change == "router":
        kb.provider._settings.deepseek_router_model = "new-router"
    elif change == "provider":
        kb.provider = CatalogProvider()
    elif change == "prompt":
        import app.catalog_knowledge_base as catalog
        monkeypatch.setattr(catalog, "PLAN_PROMPT", catalog.PLAN_PROMPT + "\nNew policy")
    else:
        kb.policy = dataclasses.replace(kb.policy, max_per_source=1)
    assert (await search(kb))[1]["cache"] == "miss"
    assert kb.provider.calls == (2 if change == "provider" else 4)
    assert original.calls >= 2


@pytest.mark.parametrize("mode", ["timeout", "error", "invalid_json", "invalid_evidence", "truncated"])
async def test_failures_are_not_negative_cached(mode, caplog):
    kb = synthetic_kb(CatalogProvider(mode))
    for _ in range(2):
        hits, metric = await search(kb)
        assert hits == [] and metric["status"] == "withheld_on_error"
        assert metric["cache"] == "miss"
    assert kb.provider.calls == 2 and kb._result_cache.stats()["entries"] == 0
    assert "PRIVATE_ERROR" not in caplog.text
    kb.provider.mode = "ok"
    assert (await search(kb))[0]


async def test_successful_empty_result_uses_short_ttl():
    kb = synthetic_kb(CatalogProvider("empty"))
    now = [0.0]
    kb._result_cache._clock = lambda: now[0]
    assert (await search(kb))[0] == []
    assert (await search(kb))[1]["cache"] == "hit"
    assert kb.provider.calls == 1
    now[0] = 46
    assert (await search(kb))[1]["cache"] == "miss"
    assert kb.provider.calls == 2


@pytest.mark.parametrize("scope", [None, (), ("", "session")])
async def test_no_identity_scope_bypasses_cache(scope):
    kb = synthetic_kb()
    for _ in range(2):
        assert (await search(kb, cache_scope=scope))[1]["cache"] == "bypass_no_scope"
    assert kb.provider.calls == 4


async def test_cache_switch_and_direct_evaluation_search_bypass():
    kb = synthetic_kb()
    kb._result_cache_enabled = False
    assert (await search(kb))[1]["cache"] == "bypass_disabled"
    assert (await search(kb))[1]["cache"] == "bypass_disabled"
    kb._result_cache_enabled = True
    await kb.search(module="module_1", query=QUERY)
    await kb.search(module="module_1", query=QUERY)
    assert kb.provider.calls == 8


async def test_singleflight_and_one_waiter_cancel_does_not_cancel_others():
    provider = CatalogProvider()
    provider.release = asyncio.Event()
    kb = synthetic_kb(provider)
    first = asyncio.create_task(search(kb))
    await provider.entered.wait()
    second = asyncio.create_task(search(kb))
    await asyncio.sleep(0)
    assert kb._result_cache.stats()["coalesced"] == 1
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    provider.release.set()
    hits, metric = await second
    assert hits and metric["cache"] == "coalesced" and provider.calls == 2
    assert (await search(kb))[1]["cache"] == "hit"


async def test_last_waiter_cancel_stops_work_and_does_not_cache():
    provider = CatalogProvider()
    provider.release = asyncio.Event()
    kb = synthetic_kb(provider)
    task = asyncio.create_task(search(kb))
    await provider.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert kb._result_cache.stats()["entries"] == 0
    assert kb._result_cache.stats()["inflight"] == 0
    provider.release.set()
    assert (await search(kb))[1]["cache"] == "miss"


async def test_real_timeout_does_not_poison_retry():
    provider = CatalogProvider()
    provider.release = asyncio.Event()
    kb = synthetic_kb(provider)
    kb.timeout_seconds = 0.01
    assert (await search(kb))[1]["status"] == "withheld_on_error"
    assert kb._result_cache.stats()["entries"] == 0
    provider.release.set()
    assert (await search(kb))[0]


async def test_lru_entry_and_payload_limits_and_positive_ttl():
    now = [0.0]
    cache = ExactRetrievalCache(max_entries=2, ttl_seconds=10, clock=lambda: now[0])
    value = SearchResult((KnowledgeChunk("one", "small evidence", "BA"),))
    async def compute():
        return value
    for key in ("a", "b", "a", "c"):
        await cache.get_or_compute(key, compute)
    assert list(cache._entries) == ["a", "c"] and cache.stats()["evictions"] == 1
    now[0] = 11
    assert cache.stats()["entries"] == 0
    assert (await cache.get_or_compute("a", compute))[1] == "miss"
    cache.max_bytes = 1
    cache.clear()
    await cache.get_or_compute("a", compute)
    assert cache.stats()["entries"] == 0


async def test_clear_detaches_old_flights_and_prevents_repopulation():
    cache = ExactRetrievalCache()
    entered, release = asyncio.Event(), asyncio.Event()
    async def old():
        entered.set()
        await release.wait()
        return SearchResult((KnowledgeChunk("old", "old", "old"),))
    async def new():
        return SearchResult((KnowledgeChunk("new", "new", "new"),))
    task = asyncio.create_task(cache.get_or_compute("same", old))
    await entered.wait()
    cache.clear()
    await cache.get_or_compute("same", new)
    release.set()
    await task
    result, metric = await cache.get_or_compute("same", new)
    assert metric == "hit" and result.hits[0].id == "new"


async def test_flight_capacity_bypasses_without_growing_pending_registry():
    cache = ExactRetrievalCache(max_entries=1)
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow():
        entered.set()
        await release.wait()
        return SearchResult()
    async def quick():
        return SearchResult()
    task = asyncio.create_task(cache.get_or_compute("slow", slow))
    await entered.wait()
    assert (await cache.get_or_compute("quick", quick))[1] == "bypass_capacity"
    assert cache.stats()["inflight"] == 1
    release.set()
    await task


async def test_compute_exception_is_not_cached_and_can_be_retried():
    cache = ExactRetrievalCache()
    async def broken():
        raise ValueError("synthetic failure")
    for _ in range(2):
        with pytest.raises(ValueError):
            await cache.get_or_compute("key", broken)
    assert cache.stats()["entries"] == cache.stats()["inflight"] == 0


@pytest.mark.parametrize("unchanged,commit_error,expected", [
    (False, False, ["commit", "invalidate", "refresh"]),
    (True, False, ["commit", "refresh"]),
    (False, True, ["commit"]),
])
async def test_admin_route_only_invalidates_after_changed_commit(monkeypatch, unchanged, commit_error, expected):
    from app.routes import admin_knowledge as route
    from app.schemas import AdminKnowledgeImport
    order = []
    async def commit():
        order.append("commit")
        if commit_error:
            raise SQLAlchemyError("synthetic rollback")
    async def refresh(_source):
        order.append("refresh")
    result = SimpleNamespace(source=object(), chunk_count=1, unchanged=unchanged)
    monkeypatch.setattr(route, "audit_identity", AsyncMock(return_value="admin"))
    monkeypatch.setattr(route, "import_knowledge_source", AsyncMock(return_value=result))
    monkeypatch.setattr(route, "invalidate_knowledge_cache", lambda: order.append("invalidate"))
    monkeypatch.setattr(route, "_item", lambda *a, **kw: result)
    args = dict(payload=AdminKnowledgeImport(name="test", category="BA", markdown="body"),
                caller=SimpleNamespace(account=object()), db=SimpleNamespace(commit=commit, refresh=refresh))
    if commit_error:
        with pytest.raises(SQLAlchemyError):
            await route.import_knowledge(**args)
    else:
        assert await route.import_knowledge(**args) is result
    assert order == expected


async def test_replaced_chunk_identity_changes_manifest_without_timestamp_change(db_sessionmaker):
    await publish(db_sessionmaker, "stable content")
    async with db_sessionmaker() as db:
        before = await knowledge_revision(db)
        original = (await db.execute(select(KnowledgeChunkRecord))).scalar_one()
        replacement = KnowledgeChunkRecord(id=original.id + 1000, source_id=original.source_id,
                                            ordinal=0, heading=original.heading, content=original.content)
        await db.execute(delete(KnowledgeChunkRecord))
        db.add(replacement)
        await db.commit()
    async with db_sessionmaker() as db:
        assert await knowledge_revision(db) != before


async def test_empty_corpus_is_a_valid_snapshot_and_new_source_becomes_visible(db_sessionmaker):
    kb = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    assert (await search(kb))[0] == []
    assert (await search(kb))[1]["cache"] == "hit"
    await publish(db_sessionmaker, "# 行为激活原理\n新的资料")
    hits, metrics = await search(kb, query="行为激活原理")
    assert hits and metrics["cache"] == "miss"


async def test_index_ttl_audits_unchanged_content_and_preserves_result_cache(db_sessionmaker):
    await publish(db_sessionmaker, "# 行为激活原理\n资料")
    kb = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    await search(kb, query="行为激活原理")
    assert (await search(kb, query="行为激活原理"))[1]["cache"] == "hit"
    before = kb._index
    kb._index_loaded_at -= 301
    assert (await search(kb, query="行为激活原理"))[1]["cache"] == "hit"
    assert kb._index is before
    assert kb.cache_monitoring_stats()["diagnostics"]["unchanged_index_refreshes"] == 1


@pytest.mark.parametrize("mode", ["p0", "enhanced", "catalog"])
async def test_two_worker_instances_observe_committed_admin_update(db_sessionmaker, mode):
    await publish(db_sessionmaker, "# 行为激活原理\n旧内容 old-sentinel")
    def build():
        if mode == "catalog":
            return CatalogDatabaseKnowledgeBase(CatalogProvider(), lambda: db_sessionmaker)
        return DatabaseKnowledgeBase(lambda: db_sessionmaker, ranking_mode=mode)
    workers = [build(), build()]
    for kb in workers:
        assert "old-sentinel" in (await search(kb, query="行为激活原理"))[0][0].text
        assert (await search(kb, query="行为激活原理"))[1]["cache"] == "hit"
    # No invalidate() call: this simulates an import committed by another worker.
    await publish(db_sessionmaker, "# 行为激活原理\n新内容 new-sentinel")
    for kb in workers:
        hits, metric = await search(kb, query="行为激活原理")
        assert metric["cache"] == "miss"
        assert "new-sentinel" in hits[0].text and "old-sentinel" not in hits[0].text


async def test_noop_import_keeps_cache_and_rollback_keeps_revision(db_sessionmaker):
    await publish(db_sessionmaker, "# 行为激活原理\nunchanged-sentinel")
    kb = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    await search(kb, query="行为激活原理")
    result = await publish(db_sessionmaker, "# 行为激活原理\nunchanged-sentinel")
    assert result.unchanged
    assert (await search(kb, query="行为激活原理"))[1]["cache"] == "hit"
    async with db_sessionmaker() as db:
        before = await knowledge_revision(db)
        await import_knowledge_source(db, name="cache-test", category="BA", markdown="rolled back", updated_by="test")
        await db.rollback()
    async with db_sessionmaker() as db:
        assert await knowledge_revision(db) == before
    assert (await search(kb, query="行为激活原理"))[1]["cache"] == "hit"


async def test_reclassification_and_delete_invalidate_other_worker(db_sessionmaker):
    await publish(db_sessionmaker, "# 行为激活原理\nprivate-module-sentinel")
    kb = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    assert (await search(kb, query="行为激活原理"))[0]
    await publish(db_sessionmaker, "# 行为激活原理\nprivate-module-sentinel", category="PA")
    assert (await search(kb, query="行为激活原理"))[0] == []
    assert (await search(kb, query="行为激活原理", module="module_2"))[0]
    async with db_sessionmaker() as db:
        await db.execute(delete(KnowledgeChunkRecord))
        await db.execute(delete(KnowledgeSourceRecord))
        await db.commit()
    assert (await search(kb, query="行为激活原理", module="module_2"))[0] == []


async def test_import_during_model_call_withholds_old_snapshot(db_sessionmaker):
    await publish(db_sessionmaker, "# 行为激活原理\nold-sentinel")
    provider = CatalogProvider()
    provider.release = asyncio.Event()
    kb = CatalogDatabaseKnowledgeBase(provider, lambda: db_sessionmaker)
    task = asyncio.create_task(search(kb))
    await provider.entered.wait()
    await publish(db_sessionmaker, "# 行为激活原理\nnew-sentinel")
    provider.release.set()
    hits, metric = await task
    assert hits == [] and metric["status"] == "withheld_corpus_changed"
    assert kb._result_cache.stats()["entries"] == 0
    hits, metric = await search(kb)
    assert "new-sentinel" in hits[0].text and metric["cache"] == "miss"


async def test_revision_read_failure_never_serves_cached_content(db_sessionmaker, monkeypatch, caplog):
    await publish(db_sessionmaker, "# 行为激活原理\nold-sentinel")
    kb = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    assert (await search(kb, query="行为激活原理"))[0]
    async def fail():
        raise SQLAlchemyError("PRIVATE_DB_ERROR")
    monkeypatch.setattr(kb, "_read_revision", fail)
    hits, metric = await search(kb, query="行为激活原理")
    assert hits == [] and metric["status"] == "withheld_revision_error"
    assert kb._result_cache.stats()["entries"] == 0
    assert "PRIVATE_DB_ERROR" not in caplog.text


async def test_index_load_retries_once_then_fails_closed_on_churn(db_sessionmaker, monkeypatch):
    kb = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    calls = []
    async def changing():
        calls.append(1)
        return str(len(calls))
    monkeypatch.setattr(kb, "_read_revision", changing)
    with pytest.raises(KnowledgeRevisionChanged):
        await kb._load_index()
    assert len(calls) == 4 and kb._index is None


async def test_cache_hit_still_calls_current_mediator_and_timeout_withholds(context, provider):
    from langgraph.runtime import Runtime
    from app.graph.nodes import ModuleConfig, make_module_node
    kb = synthetic_kb()
    context = dataclasses.replace(context, knowledge_base=kb)
    context.settings.knowledge_intent_gate_enabled = False
    state = {"user_input": QUERY, "subject_id": SCOPE[0], "session_id": SCOPE[1]}
    mediated = []
    async def mediate(**kwargs):
        payload = json.loads(kwargs["user"])
        mediated.append(payload)
        if len(mediated) == 2:
            raise asyncio.TimeoutError
        candidate = next(c for c in payload["knowledge"] if c["id"] == "kb:901")
        return Completion(text=json.dumps({"decision":"use", "selections":[{
            "id":"kb:901", "quote":candidate["text"][:40], "application":"Use cautiously."}], "note":""}), model="stub")
    provider.route_detailed = mediate
    node = make_module_node("module_1", ModuleConfig())
    first = await node(state, Runtime(context=context))
    assert "RAW_CACHE_SENTINEL" in as_text(provider.systems[-1])
    second = await node({**state, "knowledge_context": {"facts":[{
        "source":"user_activity_constraints", "source_kind":"user_statement",
        "confirmation":"unconfirmed", "values":{"constraint_text":"今天新增膝盖疼痛"}}]}}, Runtime(context=context))
    assert first["telemetry"]["retrieval"]["exact_cache"]["cache"] == "miss"
    assert second["telemetry"]["retrieval"]["exact_cache"]["cache"] == "hit"
    assert len(mediated) == 2 and kb.provider.calls == 2
    assert "膝盖疼痛" in json.dumps(mediated[1], ensure_ascii=False)
    assert second["telemetry"]["knowledge_mediator"]["reason"] == "timeout"
    assert "RAW_CACHE_SENTINEL" not in as_text(provider.systems[-1])


async def test_failed_retrieval_is_reported_as_error_not_successful_empty(context, provider):
    from langgraph.runtime import Runtime
    from app.graph.nodes import ModuleConfig, make_module_node
    context = dataclasses.replace(context, knowledge_base=synthetic_kb(CatalogProvider("timeout")))
    context.settings.knowledge_intent_gate_enabled = False
    node = make_module_node("module_1", ModuleConfig())
    result = await node({"user_input": QUERY, "subject_id": SCOPE[0], "session_id": SCOPE[1]}, Runtime(context=context))
    metrics = result["telemetry"]["retrieval"]
    assert metrics["outcome"] == "error"
    assert metrics["exact_cache"]["status"] == "withheld_on_error"
    assert "RAW_CACHE_SENTINEL" not in as_text(provider.systems[-1])


@pytest.mark.parametrize("key,value", [
    ("knowledge_result_cache_ttl_seconds", 0),
    ("knowledge_result_cache_ttl_seconds", float("nan")),
    ("knowledge_result_cache_empty_ttl_seconds", 10000),
    ("knowledge_result_cache_max_entries", 0),
    ("knowledge_result_cache_max_bytes", 0),
])
def test_cache_configuration_rejects_unbounded_or_invalid_settings(key, value):
    from app.config import Settings
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{key: value})
