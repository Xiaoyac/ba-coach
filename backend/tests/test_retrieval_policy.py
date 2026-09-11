"""Regression cases for weak evidence, diversity, and end-to-end prompt omission."""
from dataclasses import replace

import pytest

from app.config import Settings
from app.knowledge_store import import_knowledge_source
from app.providers.base import as_text
from app.retrieval import (
    DatabaseKnowledgeBase, KnowledgeChunk, RankedCandidate, RetrievalPolicy,
    index_chunk, rank_candidates, select_candidates,
)
from scripts.evaluate_retrieval import measure, relevant_ids, legacy_select


def candidate(i, score, *, category="BA", source=1, coverage=0.8):
    return RankedCandidate(KnowledgeChunk(f"kb:{i}", "reference", "test", score),
                           category, source, coverage)


def test_low_category_cannot_displace_second_high_relevance_reference():
    ranked = [candidate(1, 10), candidate(2, 9),
              candidate(3, 0.12, category="MI", source=2),
              candidate(4, 0.11, category="PA", source=3)]
    assert [c.id for c in legacy_select(ranked, 3)] == ["kb:1", "kb:3", "kb:4"]
    assert [c.id for c in select_candidates(ranked, top_k=3, policy=RetrievalPolicy())] == ["kb:1", "kb:2"]


def test_source_cap_never_backfills_with_weak_references():
    ranked = [candidate(1, 10), candidate(2, 9), candidate(3, 8),
              candidate(4, 4, source=2), candidate(5, 0.09, source=3)]
    assert [c.id for c in select_candidates(ranked, top_k=4, policy=RetrievalPolicy())] == ["kb:1", "kb:2", "kb:4"]


def test_relative_cutoff_uses_best_eligible_candidate():
    ranked = [candidate(1, 100, coverage=0.01), candidate(2, 5), candidate(3, 2)]
    assert [c.id for c in select_candidates(ranked, top_k=4, policy=RetrievalPolicy())] == ["kb:2", "kb:3"]


def test_absolute_and_coverage_thresholds_inclusive():
    policy = RetrievalPolicy()
    assert len(select_candidates([candidate(1, policy.min_score, coverage=policy.min_coverage)],
                                 top_k=1, policy=policy)) == 1
    for ranked in ([], [candidate(1, 0.099)], [candidate(1, 5, coverage=0.119)]):
        assert select_candidates(ranked, top_k=3, policy=policy) == []
    assert select_candidates([candidate(1, 10)], top_k=0, policy=policy) == []


@pytest.mark.parametrize("kwargs", [{"min_score": -1}, {"min_score": float("nan")},
    {"min_coverage": 1.1}, {"relative_score": -0.1}, {"max_per_source": 0}])
def test_invalid_policy_rejected(kwargs):
    with pytest.raises(ValueError):
        RetrievalPolicy(**kwargs)


def test_settings_and_offline_defaults_match(monkeypatch):
    for key in ("KNOWLEDGE_MIN_SCORE", "KNOWLEDGE_MIN_COVERAGE", "KNOWLEDGE_RELATIVE_SCORE", "KNOWLEDGE_MAX_PER_SOURCE"):
        monkeypatch.delenv(key, raising=False)
    config = Settings(_env_file=None)
    assert RetrievalPolicy(config.knowledge_min_score, config.knowledge_min_coverage,
                           config.knowledge_relative_score, config.knowledge_max_per_source) == RetrievalPolicy()


def test_filler_and_filename_only_cannot_qualify():
    index = (index_chunk(id=1, source_id=1, source_name="火星数据库.md", category="BA",
                         heading="交流", content="谢谢你，今天先到这里，嗯嗯明白了。"),)
    for query in ("谢谢你，今天先到这里", "嗯嗯，明白了", "火星数据库"):
        ranked = rank_candidates(index, module="module_1", query=query)
        assert ranked  # There IS lexical overlap; it must still be rejected.
        assert select_candidates(ranked, top_k=2, policy=RetrievalPolicy()) == []


def test_bilingual_alias_support_and_tiny_corpus_are_preserved():
    index = (index_chunk(id=1, source_id=1, source_name="compendium", category="PA",
                         heading="Walking", content="Walking, general, 3.8 METs."),)
    ranked = rank_candidates(index, module="module_2", query="  我想把晚饭后散步作为目标  ")
    assert select_candidates(ranked, top_k=4, policy=RetrievalPolicy())
    assert not rank_candidates(index, module="module_4", query="walking")


@pytest.mark.asyncio
async def test_env_policy_is_used_by_database_search(db_sessionmaker, monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("KNOWLEDGE_MIN_SCORE", "9999")
    get_settings.cache_clear()
    async with db_sessionmaker() as db:
        await import_knowledge_source(db, name="BA", category="BA",
                                     markdown="# 活动监测\n记录活动监测。", updated_by="test")
        await db.commit()
    kb = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    assert kb.policy.min_score == 9999
    assert await kb.search(module="module_1", query="活动监测") == []


@pytest.mark.asyncio
async def test_weak_database_results_never_reach_model_prompt(context, provider, db_sessionmaker):
    from app.graph.builder import get_graph
    async with db_sessionmaker() as db:
        await import_knowledge_source(db, name="BA-weak", category="BA",
            markdown="# 交流\n谢谢你，今天先到这里。UNRELATED_REFERENCE_SENTINEL", updated_by="test")
        await db.commit()
    context = replace(context, knowledge_base=DatabaseKnowledgeBase(lambda: db_sessionmaker))
    final = await get_graph().ainvoke(
        {"user_input": "谢谢你，今天先到这里", "forced_module": "module_1", "metadata": {}},
        context=context,
    )
    assert final["retrieved_knowledge"] == []
    assert "# Retrieved Knowledge" not in as_text(provider.systems[-1])
    assert "UNRELATED_REFERENCE_SENTINEL" not in as_text(provider.systems[-1])


def test_eval_metrics_known_ranks_and_empty_semantics():
    hits = [candidate(i, 1).chunk for i in (1, 2, 3)]
    metrics = measure(hits, {"kb:2", "kb:4"}, expect_empty=False, k=3)
    assert metrics["hit_at_k"] == 1
    assert metrics["recall_at_k"] == 0.5
    assert metrics["precision_returned"] == pytest.approx(1 / 3)
    assert metrics["mrr"] == 0.5
    assert metrics["ndcg_at_k"] == pytest.approx(0.3868528072)
    assert measure([], set(), expect_empty=True, k=3)["correct_empty"] == 1
    assert measure([], {"kb:2"}, expect_empty=False, k=3)["hit_at_k"] == 0


def test_eval_stale_judgment_fails_instead_of_reporting_zero():
    with pytest.raises(ValueError, match="no longer matches"):
        relevant_ids({"id": "bad", "module": "module_1", "expect_empty": False,
                      "relevant": [{"source": "missing.md"}]}, ())
