"""Real embedded Qdrant, deterministic vectors; no model downloads in tests."""
from dataclasses import replace

import pytest

pytest.importorskip("qdrant_client")

from app.retrieval import index_chunk
from app.vector_retrieval import HybridPolicy, LocalVectorKnowledgeBase, reciprocal_rank_fusion


class FakeEmbedder:
    identity = "test-only-v1"
    dimension = 3

    def passages(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def query(self, text):
        return [0.0, 1.0, 0.0] if text == "unrelated" else [1.0, 0.0, 0.0]


def corpus():
    return tuple(index_chunk(id=i, source_id=i, source_name=f"s{i}", category=category,
        heading="", content="循序渐进地开始行动") for i, category in ((1, "BA"), (2, "PA"), (3, "MI")))


def test_rrf_uses_rank_and_deduplicates_each_channel():
    result = reciprocal_rank_fusion(["a", "a", "b"], ["b", "c"])
    assert result[0][0] == "b"
    assert dict(result)["a"] == pytest.approx(1 / 61)


def test_real_qdrant_filtering_thresholds_and_vector_only_semantic_hit():
    kb = LocalVectorKnowledgeBase(corpus(), FakeEmbedder(), ":memory:")
    try:
        with pytest.raises(RuntimeError, match="missing/incomplete"):
            kb.search_sync(module="module_1", query="迈不开腿")
        assert kb.build() is True
        assert kb.build() is False
        assert [h.id for h in kb.search_sync(module="module_1", query="迈不开腿")] == ["kb:1"]
        assert len(kb.search_sync(module="module_2", query="迈不开腿")) == 3
        assert kb.search_sync(module="module_1", query="unrelated") == []
        assert kb.search_sync(module="invalid", query="迈不开腿") == []
        assert kb.search_sync(module="module_1", query=" ") == []
        assert kb.search_sync(module="module_1", query="迈不开腿", top_k=0) == []
    finally:
        kb.close()


def test_model_and_corpus_changes_require_matching_index(tmp_path):
    original = LocalVectorKnowledgeBase(corpus(), FakeEmbedder(), tmp_path / "qdrant")
    original.build()
    collection = original.collection
    original.close()
    changed = list(corpus())
    changed[0] = replace(changed[0], content="新版正文")
    kb = LocalVectorKnowledgeBase(changed, FakeEmbedder(), tmp_path / "qdrant")
    try:
        assert kb.collection != collection
        assert not kb.ready()
    finally:
        kb.close()
    embedder = FakeEmbedder()
    embedder.identity = "test-only-v2"
    kb = LocalVectorKnowledgeBase(corpus(), embedder, tmp_path / "qdrant")
    try:
        assert kb.collection != collection
        assert not kb.ready()
    finally:
        kb.close()


def test_bad_embedding_cannot_publish_ready_marker():
    embedder = FakeEmbedder()
    embedder.passages = lambda texts: [[float("nan"), 0, 0] for _ in texts]
    kb = LocalVectorKnowledgeBase(corpus(), embedder, ":memory:")
    try:
        with pytest.raises(ValueError, match="dimension/value"):
            kb.build()
        assert not kb.ready()
    finally:
        kb.close()


def test_completed_index_reopens_and_respects_source_cap(tmp_path):
    docs = [replace(d, source_id=1) for d in corpus()]
    kb = LocalVectorKnowledgeBase(docs, FakeEmbedder(), tmp_path / "qdrant")
    kb.build()
    kb.close()
    kb = LocalVectorKnowledgeBase(docs, FakeEmbedder(), tmp_path / "qdrant")
    try:
        assert kb.ready()
        assert not kb.build()
        assert len(kb.search_sync(module="module_2", query="another wording", top_k=10)) == 2
    finally:
        kb.close()


def test_p0_matches_existing_policy_without_built_vector_index():
    from app.retrieval import RetrievalPolicy, rank_candidates, select_candidates

    docs = corpus()
    kb = LocalVectorKnowledgeBase(docs, FakeEmbedder(), ":memory:")
    try:
        expected = select_candidates(rank_candidates(docs, module="module_2", query="开始行动"),
                                     top_k=3, policy=RetrievalPolicy())
        assert kb.search_sync(module="module_2", query="开始行动", mode="p0") == expected
    finally:
        kb.close()


def test_token_windows_preserve_text_beyond_model_limit():
    tokenizers = pytest.importorskip("tokenizers")
    from app.vector_retrieval import LocalEmbedder

    embedder = LocalEmbedder.__new__(LocalEmbedder)
    embedder.tokenizer = tokenizers.Tokenizer(tokenizers.models.WordLevel(
        {"[UNK]": 0, **{f"word{i}": i + 1 for i in range(250)}}, unk_token="[UNK]"))
    embedder.tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    embedder.window_size = 120
    text = " ".join(f"word{i}" for i in range(250))
    windows = embedder._windows(text)
    assert " ".join(windows) == text
    assert all(len(embedder.tokenizer.encode(w).ids) <= 120 for w in windows)
    assert "word249" in windows[-1]


@pytest.mark.parametrize("kwargs", [{"vector_min_score": float("nan")}, {"vector_min_score": 1.1},
                                   {"candidates": 0}, {"rrf_k": 0}, {"max_per_source": 0}])
def test_policy_validation(kwargs):
    with pytest.raises(ValueError):
        HybridPolicy(**kwargs)
