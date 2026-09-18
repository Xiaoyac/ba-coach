"""Cheap deterministic contract checks. Never download weights or open user DBs."""
from types import SimpleNamespace
import json
import pytest

from app.retrieval import KnowledgeChunk, index_chunk
from app.semantic_retrieval import HybridKnowledgeRanker, SemanticPolicy, WindowCrossEncoder


def doc(i, text="活动监测可以帮助记录行动和情绪", category="BA", source=1):
    return index_chunk(id=i, source_id=source, source_name="test", category=category,
                       heading="活动监测", content=text)


class Encoder:
    identity = "test-encoder-v1"

    def __init__(self, scores=None):
        self.calls = 0
        self.values = scores

    def scores(self, query, documents):
        self.calls += 1
        return self.values if self.values is not None else [1.0] * len(documents)


@pytest.mark.parametrize("kwargs", [{"candidate_limit": 0}, {"vector_candidates": -1},
    {"max_per_source": 0}, {"min_logit": float("nan")}, {"max_logit_gap": -1}])
def test_invalid_semantic_policy(kwargs):
    with pytest.raises(ValueError):
        SemanticPolicy(**kwargs)


def test_scope_source_limit_and_empty():
    encoder = Encoder()
    ranker = HybridKnowledgeRanker([doc(1), doc(2), doc(3), doc(4, category="PA")],
                                  use_vectors=False, cross_encoder=encoder)
    assert ranker.search(module="module_1", query="", top_k=4) == []
    assert ranker.search(module="unknown", query="活动监测") == []
    assert encoder.calls == 0
    assert [x.id for x in ranker.search(module="module_1", query="活动监测", top_k=4)] == ["kb:1", "kb:2"]


def test_floor_gap_and_no_raw_bypass():
    ranker = HybridKnowledgeRanker([doc(1), doc(2, source=2)], use_vectors=False,
        cross_encoder=Encoder([-1, -5]), policy=SemanticPolicy(min_logit=0))
    assert ranker.search(module="module_1", query="活动监测") == []
    ranker.policy = SemanticPolicy(min_logit=-2, max_logit_gap=1)
    assert [x.id for x in ranker.search(module="module_1", query="活动监测")] == ["kb:1"]


@pytest.mark.parametrize("values", [[], [float("nan")], [float("inf")]])
def test_broken_encoder_does_not_return_unvalidated_candidates(values):
    ranker = HybridKnowledgeRanker([doc(1)], use_vectors=False, cross_encoder=Encoder(values))
    with pytest.raises(ValueError):
        ranker.search(module="module_1", query="活动监测")


def test_explicit_cache_fingerprint_latency_and_validation(tmp_path):
    encoder = Encoder()
    ranker = HybridKnowledgeRanker([doc(1)], use_vectors=False, cross_encoder=encoder, score_cache=tmp_path)
    ranker.search(module="module_1", query="活动监测")
    original_ms = ranker.last_trace["inference_ms_original"]
    ranker.search(module="module_1", query="活动监测")
    assert encoder.calls == 1 and ranker.last_trace["cache_hit"]
    assert ranker.last_trace["inference_ms_original"] == original_ms
    encoder.identity = "test-encoder-v2"
    ranker.search(module="module_1", query="活动监测")
    assert encoder.calls == 2 and not ranker.last_trace["cache_hit"]
    cache = tmp_path / (ranker.last_trace["fingerprint"] + ".json")
    payload = json.loads(cache.read_text())
    payload["scores"] = [float("nan")]
    cache.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        ranker.search(module="module_1", query="活动监测")


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return SimpleNamespace(ids=list(range(len(text))), offsets=[(i, i + 1) for i in range(len(text))])


def test_cross_encoder_covers_tail_and_preserves_original_negation():
    windows = []

    def rerank(pairs, **kwargs):
        windows.extend(pairs)
        return [8.0 if "TAIL" in text else -8.0 for _, text in pairs]

    encoder = WindowCrossEncoder.__new__(WindowCrossEncoder)
    encoder.tokenizer = CharacterTokenizer()
    encoder.encoder = SimpleNamespace(rerank_pairs=rerank)
    text = "x" * 1100 + "TAIL"
    assert encoder.scores("不想跑步，想游泳", [text]) == [8]
    assert all(query == "不想跑步，想游泳" for query, _ in windows)
    assert windows[-1][1].endswith("TAIL")
    covered = "".join(passage for _, passage in windows)
    assert len(covered) >= len(text)
    with pytest.raises(ValueError, match="160-token"):
        encoder.scores("x" * 161, [text])
