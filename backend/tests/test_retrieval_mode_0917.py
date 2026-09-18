import pytest
from dataclasses import replace
from app.config import Settings
from app.knowledge_store import import_knowledge_source
from app.retrieval import DatabaseKnowledgeBase, index_chunk
from app.retrieval_enhanced import EnhancedKnowledgeRanker


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        DatabaseKnowledgeBase(ranking_mode="unknown")
    with pytest.raises(ValueError):
        Settings(_env_file=None, knowledge_retrieval_mode="semantic")


@pytest.mark.asyncio
async def test_enhanced_database_cache_replacement_uses_live_ids(db_sessionmaker):
    async def publish(body):
        async with db_sessionmaker() as db:
            await import_knowledge_source(db, name="BA-test", category="BA", markdown=body, updated_by="test")
            await db.commit()

    await publish("# 活动监测\n活动监测可以记录行为和情绪。")
    kb = DatabaseKnowledgeBase(lambda: db_sessionmaker, ranking_mode="enhanced")
    hits = await kb.search(module="module_1", query="活动监测")
    assert hits and "活动监测" in hits[0].text
    original_ranker = kb._enhanced_ranker
    expected = EnhancedKnowledgeRanker(kb._index).search(module="module_1", query="活动监测")
    assert hits == expected
    await publish("# 回避行为\n回避行为暂时缓解压力。")
    kb.invalidate()
    assert not await kb.search(module="module_1", query="活动监测")
    assert kb._enhanced_ranker is not original_ranker
    assert await kb.search(module="module_1", query="回避行为")


@pytest.mark.asyncio
async def test_ttl_snapshot_replaces_ranker_and_preserves_scope():
    kb = DatabaseKnowledgeBase(ranking_mode="enhanced")
    docs = (index_chunk(id=9071, source_id=808, source_name="source", category="PA",
        heading="游泳", content="Swimming is physical activity."),)

    async def load(**kwargs):
        return docs

    kb._load_index = load
    assert not await kb.search(module="module_1", query="游泳")
    hits = await kb.search(module="module_2", query="游泳")
    assert hits and hits[0].id == "kb:9071"
    before = kb._enhanced_ranker
    docs = tuple(list(docs))
    await kb.search(module="module_2", query="游泳")
    assert kb._enhanced_ranker is not before


@pytest.mark.asyncio
async def test_enhanced_thresholds_are_independent_and_configurable(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("KNOWLEDGE_RETRIEVAL_MODE", "enhanced")
    monkeypatch.setenv("KNOWLEDGE_ENHANCED_MIN_SCORE", "99999")
    get_settings.cache_clear()
    try:
        kb = DatabaseKnowledgeBase()
        assert kb.enhanced_policy.min_score == 99999
        assert kb.policy.min_score != 99999
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_enhanced_results_still_require_successful_mediator(context, provider, db_sessionmaker):
    from app.graph import get_graph
    from app.providers.base import as_text
    async with db_sessionmaker() as db:
        await import_knowledge_source(db, name="BA-test", category="BA",
            markdown="# 活动监测\n活动监测记录行动和情绪。ENHANCED_RAW_MUST_NOT_ESCAPE", updated_by="test")
        await db.commit()
    kb = DatabaseKnowledgeBase(lambda: db_sessionmaker, ranking_mode="enhanced")
    assert await kb.search(module="module_1", query="什么是活动监测？")

    async def timeout(**kwargs):
        raise TimeoutError

    provider.route_detailed = timeout
    context = replace(context, knowledge_base=kb)
    result = await get_graph().ainvoke({"user_input":"什么是活动监测？", "forced_module":"module_1"}, context=context)
    assert result["telemetry"]["knowledge_mediator"]["reason"] == "timeout"
    assert result["telemetry"]["retrieval"]["ranking_mode"] == "enhanced"
    assert "ENHANCED_RAW_MUST_NOT_ESCAPE" not in as_text(provider.systems[-1])
