"""Synthetic adversarial contracts; never use a model, Qdrant, or eval cases."""
from __future__ import annotations

import pytest

from app.retrieval import KnowledgeChunk, index_chunk
from app.retrieval_enhanced import EnhancedKnowledgeRanker
from app.semantic_retrieval import HybridKnowledgeRanker, SemanticPolicy


def doc(doc_id, source_id, category, heading, content):
    return index_chunk(id=doc_id, source_id=source_id, source_name=f"source-{source_id}",
                       category=category, heading=heading, content=content)


class Scores:
    identity = "synthetic-cross-encoder"

    def scores(self, query, documents):
        return [1.0] * len(documents)


class CrossScopeVector:
    """A stale/broken vector adapter that ignores its requested module."""
    def search_sync(self, **kwargs):
        return [KnowledgeChunk("kb:2", "physical activity", "source-2", 1.0)]


def test_enhanced_has_no_dependency_on_case_like_ids_or_source_names():
    ranker = EnhancedKnowledgeRanker((
        doc(917001, 41, "BA", "活动监测", "活动监测记录行为和情绪"),
        doc(917002, 42, "BA", "无关", "这里没有活动监测的正文证据"),
    ))
    hits = ranker.search(module="module_1", query="活动监测", top_k=2)
    assert hits and hits[0].id == "kb:917001"


def test_unknown_vector_id_fails_closed_instead_of_becoming_a_candidate():
    class UnknownVector:
        def search_sync(self, **kwargs):
            return [KnowledgeChunk("kb:does-not-exist", "x", "unknown", 1.0)]

    ranker = HybridKnowledgeRanker(
        [doc(1, 1, "BA", "活动监测", "活动监测记录行为和情绪")],
        use_vectors=False, vector_store=UnknownVector(), cross_encoder=Scores(),
    )
    with pytest.raises(ValueError, match="unknown knowledge ID"):
        ranker.search(module="module_1", query="活动监测")


def test_hybrid_rechecks_module_scope_after_dense_fusion():
    ranker = HybridKnowledgeRanker(
        [doc(1, 1, "BA", "活动监测", "活动监测记录行为和情绪"),
         doc(2, 2, "PA", "活动监测", "活动监测记录行为和情绪")],
        use_vectors=False, vector_store=CrossScopeVector(), cross_encoder=Scores(),
        policy=SemanticPolicy(candidate_limit=8, vector_candidates=4, min_logit=-10, max_logit_gap=99),
    )
    hits = ranker.search(module="module_1", query="活动监测", top_k=4)
    assert all(hit.id != "kb:2" for hit in hits)


def test_heading_only_evidence_can_qualify_for_an_explicit_definition_question():
    ranker = EnhancedKnowledgeRanker((
        doc(1, 1, "BA", "什么是活动监测", "正文不重复该标题关键词。"),
    ))
    assert [hit.id for hit in ranker.search(module="module_1", query="什么是活动监测")] == ["kb:1"]


def test_unknown_legacy_category_is_never_retrieved():
    ranker = EnhancedKnowledgeRanker([doc(999, 99, "unknown-category", "活动监测", "活动监测记录行为和情绪")])
    assert ranker.search(module="module_1", query="活动监测") == []
