from types import SimpleNamespace

import pytest

from app.catalog_knowledge_base import CatalogDatabaseKnowledgeBase
from app.config import Settings
from app import retrieval


def _settings(mode):
    return SimpleNamespace(
        knowledge_retrieval_mode=mode,
        knowledge_min_score=0.1,
        knowledge_min_coverage=0.12,
        knowledge_relative_score=0.2,
        knowledge_max_per_source=2,
        knowledge_enhanced_min_score=0.1,
        knowledge_enhanced_min_coverage=0.25,
        knowledge_enhanced_relative_score=0.55,
    )


def test_catalog_mode_is_accepted_by_settings_without_loading_environment():
    assert Settings(_env_file=None, knowledge_retrieval_mode="catalog").knowledge_retrieval_mode == "catalog"


def test_factory_constructs_catalog_only_for_explicit_catalog_mode(monkeypatch):
    provider = object()
    monkeypatch.setattr(retrieval, "get_settings", lambda: _settings("catalog"))
    monkeypatch.setattr(retrieval, "_knowledge_base", None)

    import app.providers as providers
    monkeypatch.setattr(providers, "get_provider", lambda: provider)

    knowledge = retrieval.get_knowledge_base()

    assert isinstance(knowledge, CatalogDatabaseKnowledgeBase)
    assert knowledge.provider is provider
    assert knowledge.ranking_mode == "catalog"
    assert retrieval.get_knowledge_base() is knowledge


@pytest.mark.parametrize("mode", ["p0", "enhanced"])
def test_factory_preserves_lexical_modes_without_provider_acquisition(monkeypatch, mode):
    monkeypatch.setattr(retrieval, "get_settings", lambda: _settings(mode))
    monkeypatch.setattr(retrieval, "_knowledge_base", None)

    import app.providers as providers
    monkeypatch.setattr(
        providers,
        "get_provider",
        lambda: pytest.fail("p0/enhanced factory path must not acquire a provider"),
    )

    knowledge = retrieval.get_knowledge_base()

    assert type(knowledge) is retrieval.DatabaseKnowledgeBase
    assert knowledge.ranking_mode == mode
    assert knowledge._enhanced_ranker is None
