"""Application limits are per selection and shared by both mediator paths."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app import hybrid_mediator
from app.config import Settings
from app.knowledge_mediator import KnowledgeGuidance, KnowledgeSelection, MEDIATOR_CONTRACT, mediate_knowledge
from app.providers.base import Completion
from app.retrieval import KnowledgeChunk


def envelope(length, count, character="用"):
    return {"decision": "use", "selections": [
        {"id": str(i), "quote": "行动可能帮助情绪", "application": character * length}
        for i in range(count)], "note": ""}


@pytest.mark.parametrize("length,count,valid", [(120, 1, True), (121, 1, False), (120, 2, True), (120, 3, False)])
@pytest.mark.parametrize("character", ["用", "a", "😀"])
def test_schema_boundary(length, count, valid, character):
    payload = envelope(length, count, character)
    if valid:
        result = KnowledgeGuidance.model_validate(payload)
        assert [s.application for s in result.selections] == [character * length] * count
    else:
        with pytest.raises(ValidationError):
            KnowledgeGuidance.model_validate(payload)


@pytest.mark.parametrize("hybrid", [False, True])
@pytest.mark.parametrize("length,count,valid", [(120, 1, True), (121, 1, False), (120, 2, True), (120, 3, False)])
async def test_both_mediator_paths_preserve_or_reject(monkeypatch, hybrid, length, count, valid):
    payload = envelope(length, count)
    provider = SimpleNamespace(name="stub", model="stub", route_detailed=AsyncMock(return_value=Completion(
        text=json.dumps(payload, ensure_ascii=False), model="stub", finish_reason="stop")),
        _client=SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(hybrid_mediator, "build_provider", lambda _: provider)
    settings = Settings(_env_file=None).model_copy(update={"knowledge_mediator_enabled": True})
    kwargs = dict(settings=settings, state={"user_input": "行动为什么有用？"}, module="module_2",
                  knowledge=[KnowledgeChunk(str(i), "行动可能帮助情绪，不保证立即改善。", "BA") for i in range(count)])
    if hybrid:
        selected, block, metrics = await hybrid_mediator.mediate_hybrid_knowledge(**kwargs)
    else:
        selected, block, metrics = await mediate_knowledge(provider=provider, **kwargs)
    if valid:
        assert metrics["status"] == "completed"
        assert len(selected) == count
        assert metrics["applications"] == ["用" * length] * count
        assert len(json.loads(block.split("\n", 2)[2])["selections"]) == count
        assert block.count("用" * length) == count  # no clipping before the main reply
    else:
        assert selected == [] and block == "" and metrics["reason"] == "invalid_schema"
    assert '"application":"1-120字' in provider.route_detailed.call_args.kwargs["system"]


async def test_long_application_does_not_bypass_exact_quote_check():
    payload = envelope(120, 1)
    payload["selections"][0]["quote"] = "行动一定改善情绪"
    provider = SimpleNamespace(name="stub", model="stub", route_detailed=AsyncMock(return_value=Completion(
        text=json.dumps(payload, ensure_ascii=False), model="stub", finish_reason="stop")))
    selected, block, metrics = await mediate_knowledge(provider=provider,
        settings=Settings(_env_file=None), state={"user_input": "行动为什么有用？"}, module="module_2",
        knowledge=[KnowledgeChunk("0", "行动可能帮助情绪，不保证立即改善。", "BA")])
    assert selected == [] and block == "" and metrics["reason"] == "invalid_evidence"


def test_contract_schema_and_default_prompt_agree():
    from app.prompt_store import PROMPT_DEFINITION_BY_KEY
    assert KnowledgeSelection.model_json_schema()["properties"]["application"]["maxLength"] == 120
    assert KnowledgeGuidance.model_json_schema()["properties"]["selections"]["maxItems"] == 2
    assert '"application":"1-120字' in MEDIATOR_CONTRACT
    assert "application 用1至120个Unicode字符" in PROMPT_DEFINITION_BY_KEY["knowledge_mediator"].default_content
    from pathlib import Path
    template = Path(__file__).resolve().parents[1] / "app/prompt_defaults/knowledge_mediator.md"
    assert "application用1至120字" in template.read_text()
