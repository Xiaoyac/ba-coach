"""Deterministic contract tests for the frozen 0917 comparison harness."""
from __future__ import annotations

import pytest

from app.retrieval import KnowledgeChunk, index_chunk
from scripts.compare_retrieval_0917 import bootstrap_deltas, compare, evaluate_retriever


def case(case_id: str, *, query: str, expect_empty: bool, relevant=None, kind="test") -> dict:
    return {"id": case_id, "split": "test", "module": "module_1", "kind": kind,
            "query": query, "expect_empty": expect_empty, "relevant": relevant or []}


def corpus():
    return (index_chunk(id=1, source_id=1, source_name="BA.md", category="BA",
                        heading="活动监测", content="记录活动和感受。"),)


def test_gate_skip_is_recorded_as_false_empty_without_calling_retriever():
    calls = []

    def search(module, query, top_k):
        calls.append((module, query, top_k))
        return [KnowledgeChunk("kb:1", "记录活动和感受。", "BA.md", 1.0)]

    report = evaluate_retriever(
        [case("positive-greeting", query="你好", expect_empty=False,
              relevant=[{"source": "BA.md", "heading": "活动监测"}])], corpus(), search)

    assert calls == []
    assert report["summary"]["false_empty"] == 1
    assert report["summary"]["false_skips"] == 1
    assert report["cases"][0]["gate"]["retrieve"] is False


def test_comparison_reports_paired_delta_and_empty_failure_counts():
    cases = [
        case("positive", query="怎样记录活动", expect_empty=False,
             relevant=[{"source": "BA.md", "heading": "活动监测"}]),
        case("negative", query="帮我订机票", expect_empty=True),
    ]

    def baseline(module, query, top_k):
        return []

    def enhanced(module, query, top_k):
        return [KnowledgeChunk("kb:1", "记录活动和感受。", "BA.md", .9)] if "记录" in query else []

    report = compare(cases, corpus(), baseline, enhanced, gate_enabled=False, bootstrap_repeats=50)
    assert report["baseline"]["summary"]["false_empty"] == 1
    assert report["enhanced"]["summary"]["false_empty"] == 0
    assert report["delta"]["hit_at_k"]["point_delta"] == 1.0
    assert report["delta"]["correct_empty"]["point_delta"] == 0.0
    # All-negative resamples have no defined Hit@K denominator and are excluded.
    assert 0 < report["delta"]["hit_at_k"]["bootstrap_samples"] <= 50


def test_broken_retriever_is_not_hidden_by_a_fallback():
    def broken(module, query, top_k):
        raise RuntimeError("enhanced ranker unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        evaluate_retriever([case("positive", query="怎样记录活动", expect_empty=False,
                                 relevant=[{"source": "BA.md", "heading": "活动监测"}])],
                           corpus(), broken, gate_enabled=False)


def test_bootstrap_requires_exact_pairing():
    rows = [{"id": "a", "expect_empty": False, "hits": [], "gate": {"retrieve": True},
             "elapsed_ms": 1, "metrics": {"returned": 0, "hit_at_k": 0, "recall_at_k": 0,
             "precision_returned": None, "mrr": 0, "ndcg_at_k": 0, "correct_empty": None}}]
    mismatched = [{**rows[0], "id": "b"}]
    with pytest.raises(ValueError, match="same cases"):
        bootstrap_deltas(rows, mismatched, repeats=1)
