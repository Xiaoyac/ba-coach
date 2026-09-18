import json

import pytest

from app.knowledge_references import KnowledgeReferences, reference_snapshot
from app.retrieval import KnowledgeChunk, index_chunk, rank_candidates
from app.semantic_catalog import SemanticCatalog
from test_retrieval_cache import synthetic_kb, search


def snapshot(chunks):
    return reference_snapshot(module="module_1", recalled=chunks, provided=chunks,
                              retrieval={}, mediator={})


def test_catalog_scores_are_missing_not_zero_and_order_is_preserved():
    docs = [index_chunk(id=i, source_id=i, source_name="synthetic", category="BA",
                       heading="行为激活原理", content="先行动观察心情") for i in (1, 2)]
    hits = SemanticCatalog.results('{"selected_ids":["kb:2","kb:1"]}', docs, top_k=2)
    assert [h.id for h in hits] == ["kb:2", "kb:1"]
    assert all(h.score is None and h.score_type == "model_selection" for h in hits)
    data = snapshot(hits)
    assert data["version"] == 2
    for group in ("recalled", "provided"):
        assert all(row["score"] is None and row["score_type"] == "model_selection" for row in data[group])
    json.dumps(data, allow_nan=False)


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf")])
def test_no_fake_numeric_score_when_unavailable(value):
    data = snapshot([KnowledgeChunk("test", "text", "source", value)])
    assert data["recalled"][0]["score"] is None
    json.dumps(data, allow_nan=False)


def test_real_zero_and_negative_scores_keep_their_meaning():
    chunks = [KnowledgeChunk("zero", "text", "source", 0, "lexical"),
              KnowledgeChunk("negative", "text", "source", -.5, "cross_encoder")]
    assert [row["score"] for row in snapshot(chunks)["recalled"]] == [0, -.5]


def test_old_snapshots_read_without_inventing_score_types_or_rewriting_history():
    old = {"available": True, "recalled": [{"id": "old", "text": "old", "source": "old", "score": 0}]}
    data = KnowledgeReferences.model_validate(old)
    assert data.version == 1 and data.recalled[0].score_type is None
    assert data.recalled[0].score == 0


def test_lexical_and_explicit_title_fast_path_keep_numeric_scores():
    docs = (index_chunk(id=1, source_id=1, source_name="synthetic", category="BA",
                        heading="行为激活原理", content="行为激活原理，先行动再观察心情。"),)
    ranked = rank_candidates(docs, module="module_1", query="行为激活原理")
    assert ranked[0].chunk.score > 0 and ranked[0].chunk.score_type == "lexical"
    fast = SemanticCatalog(docs).fast_path(module="module_1", query="解释行为激活原理", top_k=2)
    assert fast and fast[0].score > 0 and fast[0].score_type == "bm25f"


async def test_cache_preserves_absent_score_and_its_type():
    kb = synthetic_kb()
    first, _ = await search(kb)
    second, metrics = await search(kb)
    assert metrics["cache"] == "hit" and first == second
    assert second[0].score is None and second[0].score_type == "model_selection"
