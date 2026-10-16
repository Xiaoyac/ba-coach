"""Compatibility adapter for existing explicit K3 configurations."""
from .knowledge_llm_rerank import ListwiseReranker, PROMPT, VERSION


class K3Reranker(ListwiseReranker):
    def __init__(self, settings, provider=None):
        if settings.knowledge_rerank_model != "kimi-k3":
            raise ValueError("K3 reranking requires explicit kimi-k3 model")
        super().__init__(settings, provider)
