"""Production hybrid lifecycle, real FAISS/BM25, deterministic offline embeddings."""
import asyncio
from dataclasses import replace
import time
from unittest.mock import AsyncMock

import pytest

from app.hybrid_knowledge_base import HybridDatabaseKnowledgeBase, fuse
from app.knowledge_store import import_knowledge_source
from app.retrieval import index_chunk


class Embedder:
    identity = "synthetic-embedding-v1"
    dimension = 3

    def __init__(self):
        self.calls = 0

    def passages(self, texts):
        self.calls += len(texts)
        return [[1, 0, 0] for _ in texts]

    def query(self, text):
        return [0, 1, 0] if text == "unrelated" else [1, 0, 0]


DOCS = tuple(index_chunk(id=i, source_id=1, source_name="synthetic", category=category,
    heading="", content=text) for i, category, text in (
        (1, "BA", "从简单的小行动开始"), (2, "PA", "每天步行十分钟"),
        (3, "BCT", "设置活动提醒"), (4, "BA", "从简单的小行动开始"),
        (5, "BA", "行动之后再观察心情")))


def synthetic(tmp_path, docs=DOCS, embedder=None):
    kb = HybridDatabaseKnowledgeBase(embedder=embedder or Embedder(), storage=tmp_path)
    kb.settings = kb.settings.model_copy()
    kb._load_index = AsyncMock(return_value=docs)
    return kb


def test_relative_fusion_uses_scores_not_rrf_and_sparse_scales_are_independent():
    result = fuse([(1, .9), (2, .8), (3, .1)], [(3, 100), (1, 99), (2, 1)],
                  {1: "BA", 2: "PA", 3: "BA"}, weight=.9)
    assert result[0] == (1, .9)
    assert dict(result)[2] == pytest.approx(.8875)
    assert dict(result)[3] == pytest.approx(.1)
    assert fuse([], [], {}) == []


async def test_faiss_module_scope_text_dedup_same_source_and_empty(tmp_path):
    kb = synthetic(tmp_path)
    await kb.warmup()
    hits = await kb.search(module="module_1", query="semantic-only", top_k=5)
    assert {h.id for h in hits} == {"kb:1", "kb:5"}
    assert all(h.score_type == "relative_score_fusion" for h in hits)
    assert "kb:2" in {h.id for h in await kb.search(module="module_2", query="semantic-only", top_k=5)}
    assert "kb:3" not in {h.id for h in await kb.search(module="module_2", query="semantic-only", top_k=5)}
    # Candidate recall has no absolute cosine gate. Intent/relevance must be
    # evaluated downstream; a candidate list is not a claim of relevance.
    assert await kb.search(module="module_1", query="unrelated")
    assert await kb.search(module="unknown", query="semantic-only") == []


async def test_disk_restart_incremental_content_model_and_corrupt_vector(tmp_path):
    first = synthetic(tmp_path)
    await first.warmup()
    restarted = synthetic(tmp_path)
    await restarted.warmup()
    assert restarted._embedder.calls == 0
    modified = (replace(DOCS[0], content="新知识"), *DOCS[1:])
    updated = synthetic(tmp_path, modified)
    await updated.warmup()
    assert updated._embedder.calls == 1
    embedder = Embedder()
    embedder.identity = "synthetic-embedding-v2"
    changed_model = synthetic(tmp_path, embedder=embedder)
    await changed_model.warmup()
    assert changed_model._snapshot.identity != first._snapshot.identity
    assert embedder.calls > 0
    for file in (tmp_path / "vectors" / first._snapshot.identity).glob("*.npy"):
        file.write_bytes(b"broken")
    repaired = synthetic(tmp_path)
    await repaired.warmup()
    assert repaired._embedder.calls > 0


async def test_database_import_and_delete_never_returns_stale_snapshot(db_sessionmaker, tmp_path):
    from sqlalchemy import delete
    from app.models import KnowledgeChunkRecord, KnowledgeSourceRecord
    kb = HybridDatabaseKnowledgeBase(lambda: db_sessionmaker, embedder=Embedder(), storage=tmp_path)
    async def publish(content):
        async with db_sessionmaker() as db:
            await import_knowledge_source(db, name="hybrid-fixture", category="BA", markdown=content, updated_by="test")
            await db.commit()
    await publish("# 原始资料\n\nOLD_SOURCE")
    await kb.warmup()
    scope = ("owner", "room", "False")
    old, metrics = await kb.search_with_diagnostics(module="module_1", query="semantic-only", cache_scope=scope)
    assert old and metrics["model_calls"] == 0
    await publish("# 更新资料\n\nNEW_SOURCE")
    await kb.warmup()
    new, metrics = await kb.search_with_diagnostics(module="module_1", query="semantic-only", cache_scope=scope)
    assert new and "NEW_SOURCE" in new[0].text and old != new
    async with db_sessionmaker() as db:
        await db.execute(delete(KnowledgeChunkRecord))
        await db.execute(delete(KnowledgeSourceRecord))
        await db.commit()
    await kb.warmup()
    assert await kb.search(module="module_1", query="semantic-only") == []


async def test_errors_are_withheld_never_legacy_and_not_cached(tmp_path):
    kb = synthetic(tmp_path)
    kb._build_sync = lambda _: (_ for _ in ()).throw(RuntimeError("model unavailable"))
    for _ in range(2):
        hits, metrics = await kb.search_with_diagnostics(module="module_1", query="semantic-only", cache_scope=("u", "s"))
        assert not hits and metrics["status"] == "withheld_hybrid_error"
        assert metrics["model_calls"] == 0 and metrics["cache"] != "hit"


async def test_index_build_does_not_block_event_loop_or_first_reply(tmp_path):
    kb = synthetic(tmp_path)
    original = kb._build_sync
    def slow(index):
        time.sleep(.35)
        return original(index)
    kb._build_sync = slow
    start = time.perf_counter()
    hits, metrics = await kb.search_with_diagnostics(module="module_1", query="semantic-only")
    assert not hits and metrics["status"].startswith("withheld_")
    assert time.perf_counter() - start < .3
    await kb._build_task
    assert await kb.search(module="module_1", query="semantic-only")


async def test_graph_hybrid_never_calls_legacy_mediator(context, provider, monkeypatch, tmp_path):
    from app.graph import get_graph, nodes
    from app.providers.base import as_text
    kb = synthetic(tmp_path)
    await kb.warmup()
    context = replace(context, knowledge_base=kb, knowledge_mediator_bypass=True)
    context.settings.knowledge_intent_gate_enabled = False
    forbidden = AsyncMock(side_effect=AssertionError("legacy mediator must not execute"))
    monkeypatch.setattr(nodes, "mediate_knowledge", forbidden)
    result = await get_graph().ainvoke({"user_input": "semantic-only", "forced_module": "module_2"}, context=context)
    forbidden.assert_not_called()
    telemetry = result["telemetry"]
    assert telemetry["retrieval"]["ranking_mode"] == "astrbot_hybrid"
    assert telemetry["retrieval"]["exact_cache"]["model_calls"] == 0
    assert telemetry["knowledge_mediator"]["reason"] == "hybrid_direct"
    assert telemetry["knowledge_references"]["provided"]
    assert "从简单的小行动开始" in as_text(provider.systems[-1])


async def test_optional_reranker_failure_retains_order(tmp_path, monkeypatch):
    import httpx
    kb = synthetic(tmp_path)
    await kb.warmup()
    hits = await kb.search(module="module_1", query="semantic-only")
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectError("unavailable")))
    assert await kb._rerank("query", hits) == hits


async def test_per_category_quotas_preserve_weak_dense_and_low_coverage_sparse(tmp_path):
    docs = tuple(index_chunk(id=i, source_id=i, source_name="test", category=category,
        heading="", content=f"{text} number{i}") for i, category, text in (
            (1, "BA", "alpha"), (2, "BA", "beta"), (3, "PA", "gamma"), (4, "PA", "delta")))
    kb = synthetic(tmp_path, docs)
    kb.settings.knowledge_hybrid_dense_candidates = 1
    kb.settings.knowledge_hybrid_sparse_candidates = 1
    await kb.warmup()
    _, metrics = await kb.search_with_diagnostics(module="module_2", query="beta delta extra irrelevant tokens")
    trace = metrics["retriever_details"]
    assert trace["candidates_by_category"] == {"BA": {"dense": 1, "sparse": 1}, "PA": {"dense": 1, "sparse": 1}}
    assert set(trace["sparse_ids"]) == {"kb:2", "kb:4"}
    assert len(trace["dense_ids"]) == 2


@pytest.mark.parametrize("backend", ["compatible", "dashscope"])
async def test_real_rerank_order_and_wire_contract(tmp_path, monkeypatch, backend):
    import httpx
    kb = synthetic(tmp_path)
    kb.settings.knowledge_rerank_backend = backend
    kb.settings.knowledge_rerank_model = "dedicated-test-reranker"
    kb.settings.knowledge_rerank_url = "https://example.invalid/rerank"
    kb.settings.knowledge_rerank_api_key = "test-only"
    kb.settings.knowledge_hybrid_require_rerank = True
    await kb.warmup()
    captured = []
    async def post(self, url, *, headers, json):
        captured.append(json)
        documents = json["input"]["documents"] if backend == "dashscope" else json["documents"]
        rows = [{"index": i, "relevance_score": i / 10} for i in range(len(documents))]
        data = {"output": {"results": rows}} if backend == "dashscope" else {"results": rows}
        data["usage"] = {"total_tokens": 42}
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    hits, metrics = await kb.search_with_diagnostics(module="module_1", query="semantic-only", top_k=1)
    assert hits[0].id == "kb:5"
    assert hits[0].score_type == "cross_encoder"
    trace = metrics["retriever_details"]
    assert trace["rerank"] == "completed" and trace["rerank_usage"] == {"total_tokens": 42}
    # Rerank sees the fusion pool before final truncation.
    body = captured[0]["input"] if backend == "dashscope" else captured[0]
    assert len(body["documents"]) == 2


async def test_quality_failure_is_visible_and_never_cached(tmp_path, monkeypatch):
    import httpx
    kb = synthetic(tmp_path)
    kb.settings.knowledge_hybrid_require_rerank = True
    with pytest.raises(ValueError, match="requires"):
        await kb.warmup()
    kb.settings.knowledge_rerank_model = "test-reranker"
    kb.settings.knowledge_rerank_url = "https://example.invalid/rerank"
    kb.settings.knowledge_rerank_api_key = "test-only"
    await kb.warmup()
    post = AsyncMock(side_effect=httpx.ConnectError("unavailable"))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    for _ in range(2):
        hits, metrics = await kb.search_with_diagnostics(module="module_1", query="semantic-only", cache_scope=("u", "s"))
        assert not hits and metrics["status"] == "withheld_rerank_error" and metrics["cache"] != "hit"
    assert post.await_count == 2


def test_sparse_fts_literals_and_separate_scales():
    from app.knowledge_sparse import SparseIndex
    first = SparseIndex([1, 2], [["a", "b"], ["b"]])
    second = SparseIndex([3], [["a"]])
    assert first.search(["a"], 50)[0][0] == 1
    assert second.search(["a"], 50)[0][0] == 3
    assert first.search(['a" OR b*'], 50) == []
    assert first.search([], 50) == []


def test_dense_rank_breaks_equal_score_and_equal_rrf_tie():
    assert fuse([(2, .5), (1, .5)], [(1, 1), (2, 1)], {1: 'BA', 2: 'BA'})[0][0] == 2


@pytest.mark.parametrize('rows', [
    [{'index': 0, 'relevance_score': .7}, {'index': 0, 'relevance_score': .8}],
    [{'index': 0, 'relevance_score': .7}],
    [{'index': -1, 'relevance_score': .7}, {'index': 0, 'relevance_score': .8}],
    [{'index': 0, 'relevance_score': 'NaN'}, {'index': 1, 'relevance_score': .8}],
])
async def test_invalid_rerank_results_fail_quality_mode(tmp_path, monkeypatch, rows):
    import httpx
    kb = synthetic(tmp_path)
    kb.settings.knowledge_hybrid_require_rerank = True
    kb.settings.knowledge_rerank_model = 'test'
    kb.settings.knowledge_rerank_url = 'https://example.invalid/rerank'
    kb.settings.knowledge_rerank_api_key = 'test-only'
    await kb.warmup()
    async def post(self, url, **kw):
        return httpx.Response(200, json={'results': rows}, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    hits, metrics = await kb.search_with_diagnostics(module='module_1', query='semantic-only')
    assert not hits and metrics['status'] == 'withheld_rerank_error'
    assert metrics['model_calls'] == 1


async def test_optional_relevance_cutoff_runs_after_rerank_and_cache_tracks_policy(tmp_path, monkeypatch):
    import httpx
    kb = synthetic(tmp_path)
    kb.settings.knowledge_rerank_model = 'test'
    kb.settings.knowledge_rerank_url = 'https://example.invalid/rerank'
    kb.settings.knowledge_rerank_api_key = 'test-only'
    await kb.warmup()
    async def post(self, url, **kw):
        return httpx.Response(200, json={'results': [{'index': i, 'relevance_score': .1}
            for i in range(len(kw['json']['documents']))]}, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    kwargs = dict(module='module_1', query='semantic-only', cache_scope=('u','s'))
    hits, _ = await kb.search_with_diagnostics(**kwargs)
    assert hits
    kb.settings.knowledge_rerank_min_score = .5
    hits, metrics = await kb.search_with_diagnostics(**kwargs)
    assert not hits and metrics['status'] == 'hybrid_completed' and metrics['cache'] != 'hit'
    assert metrics['retriever_details']['rerank'] == 'completed'


async def test_rerank_has_total_deadline_and_withholds_in_quality_profile(tmp_path, monkeypatch):
    import httpx
    kb = synthetic(tmp_path)
    kb.settings.knowledge_hybrid_require_rerank = True
    kb.settings.knowledge_rerank_model = 'test'
    kb.settings.knowledge_rerank_url = 'https://example.invalid/rerank'
    kb.settings.knowledge_rerank_api_key = 'test-only'
    kb.settings.knowledge_rerank_timeout_seconds = .01
    await kb.warmup()
    async def stalled(*args, **kwargs):
        await asyncio.sleep(1)
        raise AssertionError('total deadline was not enforced')
    monkeypatch.setattr(httpx.AsyncClient, 'post', stalled)
    start = time.perf_counter()
    hits, metrics = await kb.search_with_diagnostics(module='module_1', query='semantic-only')
    assert time.perf_counter() - start < .5
    assert not hits and metrics['status'] == 'withheld_rerank_error'


async def test_sparse_reserve_rescues_evidence_outside_fusion_pool(tmp_path):
    class DifferentEmbeddings(Embedder):
        def passages(self, texts):
            return [[1,0,0] if 'alpha' in t else [.1,.9,0] for t in texts]
    docs = tuple(index_chunk(id=i, source_id=i, source_name='test', category='BA',
                            heading='', content=text) for i,text in [(1,'alpha unrelated'),(2,'target evidence')])
    kb = synthetic(tmp_path, docs, DifferentEmbeddings())
    kb.settings.knowledge_hybrid_candidates = 1
    kb.settings.knowledge_hybrid_sparse_reserve = 1
    await kb.warmup()
    _, metrics = await kb.search_with_diagnostics(module='module_1', query='target', top_k=1)
    trace = metrics['retriever_details']
    assert trace['fused_ids'] == ['kb:1']
    assert trace['rerank_pool_ids'] == ['kb:1','kb:2']


@pytest.mark.parametrize("backend,model", [("k3", "kimi-k3"), ("llm", "deepseek-v4.1-flash")])
async def test_listwise_sees_original_refusal_even_when_embedding_query_is_focused(tmp_path, backend, model):
    kb = synthetic(tmp_path)
    kb.settings.knowledge_rerank_backend = backend
    kb.settings.knowledge_rerank_model = model
    kb.settings.knowledge_hybrid_require_rerank = True
    rank = AsyncMock(return_value=[])
    from types import SimpleNamespace
    kb._llm_reranker = SimpleNamespace(rank=rank)
    await kb.warmup()
    query = '我不想跳绳，只想散步'
    hits, metrics = await kb.search_with_diagnostics(module='module_2', query=query)
    assert not hits and metrics['retriever_details']['rerank'] == 'completed'
    assert rank.call_args.args[0] == query
    assert metrics['model_calls'] == 1
