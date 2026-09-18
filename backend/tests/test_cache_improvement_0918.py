"""Offline cache freshness/diagnostic regressions. Synthetic SQLite and providers only."""
import asyncio
import json

import pytest
from sqlalchemy import update

from app.catalog_knowledge_base import CatalogDatabaseKnowledgeBase
from app.models import KnowledgeChunkRecord
from app.retrieval import DatabaseKnowledgeBase, KnowledgeChunk
from app.retrieval_cache import ExactRetrievalCache, SearchResult
from app.semantic_catalog import SemanticCatalog
from test_retrieval_cache import CatalogProvider, DOCS, publish, search, synthetic_kb


async def test_refresh_preserves_snapshot_epoch_flights_and_public_preparation(db_sessionmaker):
    await publish(db_sessionmaker, '# 行为激活原理\n先行动再观察。')
    provider = CatalogProvider()
    kb = CatalogDatabaseKnowledgeBase(provider, lambda: db_sessionmaker)
    await search(kb)
    before = (kb._index, kb._index_epoch, kb._catalog)
    kb._index_loaded_at -= 301
    _, trace = await search(kb)
    assert trace['cache'] == 'hit'
    assert (kb._index, kb._index_epoch, kb._catalog) == before
    assert provider.calls == 2
    assert kb.cache_monitoring_stats()['invalidations'] == 0
    assert kb.cache_monitoring_stats()['saved_model_calls'] == 2


async def test_direct_dms_edit_is_detected_by_full_content_audit(db_sessionmaker):
    await publish(db_sessionmaker, '# 行为激活原理\n旧知识')
    kb = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    await search(kb, query='行为激活原理')
    old_revision = kb._index_revision
    async with db_sessionmaker() as db:
        await db.execute(update(KnowledgeChunkRecord).values(content='行为激活原理\n新知识'))
        await db.commit()
    assert await kb._read_revision() == old_revision  # Deliberately no source metadata update.
    kb._index_loaded_at -= 301
    hits, trace = await search(kb, query='行为激活原理')
    assert trace['cache'] == 'miss' and trace['cache_reason'] == 'invalidated'
    assert '新知识' in hits[0].text and '旧知识' not in hits[0].text
    assert kb.cache_monitoring_stats()['invalidation_reasons'] == {'content_changed': 1}


async def test_catalog_call_crossing_audit_boundary_does_not_withhold_unchanged_knowledge(db_sessionmaker):
    await publish(db_sessionmaker, '# 行为激活原理\n先行动。')
    provider = CatalogProvider()
    provider.release = asyncio.Event()
    kb = CatalogDatabaseKnowledgeBase(provider, lambda: db_sessionmaker)
    request = asyncio.create_task(search(kb))
    await provider.entered.wait()
    kb._index_loaded_at -= 301
    provider.release.set()
    hits, trace = await request
    assert hits and trace['status'] == 'completed'
    assert kb.cache_monitoring_stats()['diagnostics']['unchanged_index_refreshes'] == 1


async def test_admin_update_miss_reason_and_repeat_hit(db_sessionmaker):
    await publish(db_sessionmaker, '# 行为激活原理\n旧内容')
    kb = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    assert (await search(kb, query='行为激活原理'))[1]['cache_reason'] == 'first_or_untracked'
    await publish(db_sessionmaker, '# 行为激活原理\n新内容')
    assert (await search(kb, query='行为激活原理'))[1]['cache_reason'] == 'invalidated'
    assert (await search(kb, query='行为激活原理'))[1]['cache_reason'] == 'exact_match'
    stats = kb.cache_monitoring_stats()
    assert stats['miss_reasons'] == {'first_or_untracked': 1, 'invalidated': 1}
    assert stats['requests'] == 3 and stats['hits'] == 1


async def test_expiry_reason_survives_stats_poll_and_is_not_counted_as_clear():
    kb = synthetic_kb()
    now = [0.0]
    kb._result_cache._clock = lambda: now[0]
    await search(kb)
    now[0] = kb._result_cache.ttl_seconds + 1
    assert kb.cache_monitoring_stats()['entries'] == 0
    assert (await search(kb))[1]['cache_reason'] == 'expired'
    assert kb.cache_monitoring_stats()['invalidations'] == 0


async def test_config_changed_reason_and_failure_retry_reason():
    kb = synthetic_kb()
    await search(kb)
    kb.provider.model = 'new-model'
    assert (await search(kb))[1]['cache_reason'] == 'key_changed'
    broken = synthetic_kb(CatalogProvider('timeout'))
    await search(broken)
    assert (await search(broken))[1]['cache_reason'] == 'uncacheable'
    assert broken.cache_monitoring_stats()['hits'] == 0
    assert broken.cache_monitoring_stats()['uncacheable'] == 2


async def test_diagnostic_fingerprints_are_bounded_and_expire_honestly():
    now = [0.0]
    cache = ExactRetrievalCache(max_entries=1, ttl_seconds=10, clock=lambda: now[0])
    async def compute():
        return SearchResult((KnowledgeChunk('1', 'public', 'public'),))
    for n in range(50):
        await cache.get_or_compute(cache.key({'query': f'private-{n}'}), compute)
    assert len(cache._history) <= 4
    assert 'private' not in str(cache._history) + str(cache.stats())
    trace = {}
    await cache.get_or_compute(cache.key({'query': 'private-48'}), compute, diagnostics=trace)
    assert trace['cache_reason'] == 'evicted'
    # Stats expires entries first, then bounded diagnostic history expires.
    now[0] = 20
    cache.stats()
    now[0] = 1000
    await cache.get_or_compute(cache.key({'query': 'private-48'}), compute, diagnostics=trace)
    assert trace['cache_reason'] == 'first_or_untracked'


def test_public_preparation_keeps_no_queries_and_does_not_mutate_payloads():
    catalog = SemanticCatalog(DOCS)
    first = catalog.plan_payload(module='module_1', query='PRIVATE_ONE')
    original = first['sections'][0]['heading']
    first['sections'][0]['heading'] = 'poison'
    second = catalog.plan_payload(module='module_1', query='PRIVATE_TWO')
    assert second['sections'][0]['heading'] == original
    assert second['query_with_recent_context'] == 'PRIVATE_TWO'
    assert 'PRIVATE_' not in str(catalog._public_scopes)
    assert catalog._public_cache_counts == {'misses': 1, 'hits': 1}
    for n in range(100):
        assert catalog.plan_payload(module=f'unknown-{n}', query='anything')['sections'] == []
    assert len(catalog._public_scopes) == 1


async def test_new_context_reuses_public_structure_but_always_rejudges():
    kb = synthetic_kb()
    _, first = await search(kb, query='没劲时怎么开始？')
    _, second = await search(kb, query='我不想散步，今天膝盖痛', cache_scope=('another-user', 'another-room'))
    assert first['cache'] == second['cache'] == 'miss'
    assert kb.provider.calls == 4
    stats = kb.cache_monitoring_stats()
    assert stats['public_preparation'] == {'hits': 1, 'requests': 2, 'hit_rate': .5,
        'entries': 1, 'saved_model_calls': 0}
    assert stats['hits'] == stats['saved_model_calls'] == 0


def test_cached_public_scope_is_payload_and_candidate_equivalent_to_uncached():
    warm = SemanticCatalog(DOCS)
    uncached = SemanticCatalog(DOCS, cache_public=False)
    for module in ('module_1', 'module_2', 'module_4', 'unknown'):
        for query in ('没动力', '不想散步\n昨天想散步', 'PRIVATE_CONTEXT', '天气'):
            assert warm.plan_payload(module=module, query=query) == uncached.plan_payload(module=module, query=query)
            chosen = [item['id'] for item in warm.plan_payload(module=module, query=query)['sections']]
            args = dict(module=module, query=query, plan_raw=json.dumps({'section_ids': chosen}))
            assert warm.candidates(**args) == uncached.candidates(**args)


async def test_public_counts_remain_cumulative_but_payloads_clear_on_update(db_sessionmaker):
    await publish(db_sessionmaker, '# 行为激活原理\n旧内容')
    kb = CatalogDatabaseKnowledgeBase(CatalogProvider(), lambda: db_sessionmaker)
    await search(kb, query='问法甲')
    await search(kb, query='问法乙')
    await publish(db_sessionmaker, '# 新的资料标题\n新的内容')
    hits, trace = await search(kb, query='问法丙')
    assert trace['cache'] == 'miss' and '新的内容' in hits[0].text
    stats = kb.cache_monitoring_stats()['public_preparation']
    assert stats['hits'] == 1 and stats['requests'] == 3 and stats['entries'] == 1
    assert '旧内容' not in str(kb._catalog._public_scopes)
