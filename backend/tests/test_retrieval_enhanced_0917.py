"""Bounded behavior tests for the independent enhanced lexical ranker."""
from __future__ import annotations

import math

import pytest

from app.retrieval import index_chunk
from app.retrieval_enhanced import EnhancedKnowledgeRanker, EnhancedPolicy, focus_query


def document(doc_id, source_id, source, category, heading, content):
    return index_chunk(id=doc_id, source_id=source_id, source_name=source, category=category,
                       heading=heading, content=content)


@pytest.mark.parametrize("kwargs", [
    {"min_coverage": -.01}, {"min_coverage": 1.01}, {"relative_score": -.01},
    {"relative_score": 1.01}, {"min_score": -1}, {"min_score": float("nan")},
    {"max_per_source": 0},
])
def test_enhanced_policy_rejects_invalid_thresholds(kwargs):
    with pytest.raises(ValueError):
        EnhancedPolicy(**kwargs)


def test_empty_query_and_unknown_module_return_no_candidates():
    ranker = EnhancedKnowledgeRanker((document(1, 1, "ba.md", "BA", "活动监测", "记录活动和情绪"),))
    assert ranker.candidates(module="module_1", query="") == []
    assert ranker.search(module="module_1", query="   ") == []
    assert ranker.candidates(module="unknown", query="活动监测") == []


def test_module_scope_excludes_ineligible_categories():
    ranker = EnhancedKnowledgeRanker((
        document(1, 1, "ba.md", "BA", "活动监测", "记录活动和情绪"),
        document(2, 2, "pa.md", "PA", "活动监测", "记录活动和情绪"),
    ))
    hits = ranker.candidates(module="module_1", query="活动监测")
    assert [hit.chunk.id for hit in hits] == ["kb:1"]


def test_filename_words_are_not_retrieval_evidence():
    ranker = EnhancedKnowledgeRanker((
        document(1, 1, "运动活动监测资料大全.md", "BA", "无关标题", "这里没有相关术语。"),
    ))
    assert ranker.candidates(module="module_1", query="活动监测") == []


def test_source_cap_and_deterministic_score_ordering():
    ranker = EnhancedKnowledgeRanker((
        document(1, 10, "same.md", "BA", "计划", "制定具体活动计划"),
        document(2, 10, "same.md", "BA", "计划", "制定具体活动计划"),
        document(3, 10, "same.md", "BA", "计划", "制定具体活动计划"),
        document(4, 20, "other.md", "BA", "计划", "制定具体活动计划"),
    ), EnhancedPolicy(min_coverage=0, relative_score=0, min_score=0))
    first = ranker.search(module="module_1", query="制定活动计划", top_k=4)
    second = ranker.search(module="module_1", query="制定活动计划", top_k=4)
    assert [hit.id for hit in first] == [hit.id for hit in second]
    assert [hit.id for hit in first[:2]] == ["kb:1", "kb:2"]
    assert sum(hit.source.startswith("same.md") for hit in first) == 2
    assert any(hit.source.startswith("other.md") for hit in first)


def test_focus_query_removes_rejected_chinese_activity_with_ascii_comma_and_history():
    focused = focus_query("我不想跑步, 想游泳\n之前也一直跑步")
    assert "跑步" not in focused
    assert "游泳" in focused


def test_definition_question_prefers_definition_heading():
    ranker = EnhancedKnowledgeRanker((
        document(1, 1, "ba.md", "BA", "什么是活动监测", "活动监测用于记录活动。"),
        document(2, 2, "ba2.md", "BA", "活动监测练习", "活动监测用于记录活动。"),
    ))
    hits = ranker.search(module="module_1", query="什么是活动监测", top_k=2)
    assert hits and hits[0].id == "kb:1"


def test_length_normalization_keeps_scores_finite_and_does_not_favor_padding():
    ranker = EnhancedKnowledgeRanker((
        document(1, 1, "short.md", "BA", "行为激活", "行为激活帮助安排活动。"),
        document(2, 2, "long.md", "BA", "背景", "行为激活 " + "无关填充 " * 3000),
    ))
    candidates = ranker.candidates(module="module_1", query="行为激活")
    assert candidates and all(math.isfinite(candidate.chunk.score) for candidate in candidates)
    assert candidates[0].chunk.id == "kb:1"
