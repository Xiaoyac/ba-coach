import asyncio

from test_knowledge_base import knowledge_admin_headers
from test_retrieval_cache import synthetic_kb, search


def test_stats_requires_admin_and_does_not_cache_http(client, auth_headers, knowledge_admin_headers, monkeypatch):
    from app.routes import admin_knowledge
    kb = synthetic_kb()
    monkeypatch.setattr(admin_knowledge, "get_knowledge_base", lambda: kb)
    path = "/api/admin/knowledge/cache-stats"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth_headers).status_code == 403
    empty = client.get(path, headers=knowledge_admin_headers)
    assert empty.status_code == 200 and empty.headers["cache-control"] == "no-store"
    assert empty.json()["requests"] == 0 and empty.json()["hit_rate"] is None
    async def exercise():
        await search(kb)
        await search(kb)
    asyncio.run(exercise())
    result = client.get(path, headers=knowledge_admin_headers).json()
    assert result["requests"] == 2 and result["hits"] == 1 and result["hit_rate"] == .5
    assert result["scope"] == "process" and result["entries"] == 1
    assert result["miss_reasons"] == {"first_or_untracked": 1}
    assert result["saved_model_calls"] == 2
    assert result["public_preparation"]["saved_model_calls"] == 0
    assert result["public_preparation"]["requests"] == 1
    assert "query" not in result and "scope_key" not in result
    kb.invalidate()
    after = client.get(path, headers=knowledge_admin_headers).json()
    assert after["entries"] == 0 and after["requests"] == 2
    kb._result_cache_enabled = False
    assert client.get(path, headers=knowledge_admin_headers).json()["enabled"] is False


async def test_withheld_old_cache_hit_is_not_counted_as_served(monkeypatch):
    kb = synthetic_kb()
    await search(kb)
    calls = 0
    original = kb._load_index
    async def load(**kwargs):
        nonlocal calls
        calls += 1
        docs = await original(**kwargs)
        if calls == 2:
            kb.invalidate()
        return docs
    monkeypatch.setattr(kb, "_load_index", load)
    hits, metrics = await search(kb)
    assert hits == [] and metrics["status"] == "withheld_corpus_changed"
    assert kb.cache_monitoring_stats()["hits"] == 0
    assert kb.cache_monitoring_stats()["requests"] == 2
    assert kb.cache_monitoring_stats()["saved_model_calls"] == 0
