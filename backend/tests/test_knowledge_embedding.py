"""Embedding wire contracts and persistent-index isolation, with mocked HTTP."""
import httpx
import pytest

from app.config import Settings
from app.knowledge_embedding import RemoteEmbedder


def settings(backend="openai", **overrides):
    return Settings(_env_file=None, knowledge_embedding_backend=backend,
        knowledge_embedding_url="https://example.invalid/embeddings",
        knowledge_embedding_api_key="test-only", knowledge_embedding_model="test-model",
        knowledge_embedding_dimensions=2, knowledge_embedding_batch_size=2, **overrides)


@pytest.mark.parametrize("backend", ["openai", "dashscope"])
def test_batching_order_and_query_document_encoding(monkeypatch, backend):
    sent = []
    def post(self, url, *, headers, json):
        sent.append(json)
        texts = json["input"]["texts"] if backend == "dashscope" else json["input"]
        key = "text_index" if backend == "dashscope" else "index"
        rows = [{key: i, "embedding": [3, 4]} for i in reversed(range(len(texts)))]
        data = {"output": {"embeddings": rows}} if backend == "dashscope" else {"data": rows}
        data["usage"] = {"total_tokens": len(texts)}
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.Client, "post", post)
    embedder = RemoteEmbedder(settings(backend))
    assert embedder.passages(["A", "B", "C"]) == [[.6, .8]] * 3
    assert embedder.last_usage == {"reported_tokens": 3, "usage_complete": True}
    assert len(sent) == 2
    assert embedder.query("question") == [.6, .8]
    if backend == "dashscope":
        assert sent[0]["parameters"]["text_type"] == "document"
        assert sent[-1]["parameters"]["text_type"] == "query"


@pytest.mark.parametrize("rows", [
    [{"index": 0, "embedding": [1, 0]}, {"index": 0, "embedding": [1, 0]}],
    [{"index": -1, "embedding": [1, 0]}],
    [{"index": True, "embedding": [1, 0]}],
    [{"index": 0, "embedding": [1]}],
    [{"index": 0, "embedding": [0, 0]}],
    [{"index": 0, "embedding": [1, "invalid"]}],
])
def test_malformed_vectors_are_not_published(monkeypatch, rows):
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, **kw:
        httpx.Response(200, json={"data": rows}, request=httpx.Request("POST", url)))
    with pytest.raises(ValueError):
        RemoteEmbedder(settings()).query("test")


def test_provider_identity_and_missing_configuration():
    cfg = settings()
    first = RemoteEmbedder(cfg)
    for key, value in [("knowledge_embedding_model", "different"),
                       ("knowledge_embedding_dimensions", 3),
                       ("knowledge_embedding_backend", "dashscope"),
                       ("knowledge_embedding_url", "https://another.invalid/embeddings")]:
        assert RemoteEmbedder(cfg.model_copy(update={key: value})).identity != first.identity
    assert RemoteEmbedder(cfg.model_copy(update={"knowledge_embedding_api_key": "rotated"})).identity == first.identity
    with pytest.raises(ValueError, match="required"):
        RemoteEmbedder(cfg.model_copy(update={"knowledge_embedding_api_key": None}))


def test_missing_usage_is_unknown_not_zero(monkeypatch):
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, **kw:
        httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]},
                       request=httpx.Request("POST", url)))
    embedder = RemoteEmbedder(settings())
    embedder.query("test")
    assert embedder.last_usage == {"reported_tokens": None, "usage_complete": False}
