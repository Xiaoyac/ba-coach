import dataclasses
import json

import pytest

from app.graph import get_graph
from app.knowledge_mediator import mediate_knowledge
from app.providers.base import as_text
from app.retrieval import KnowledgeChunk
from test_evaluations import admin_headers

CHUNKS = [KnowledgeChunk(id="one", text="行动可能帮助情绪，不保证立即改善。", source="BA"),
          KnowledgeChunk(id="two", text="记录活动和感受。", source="记录")]


@pytest.fixture
def settings(context):
    return context.settings


@pytest.mark.parametrize("module", ["module_1","module_2","module_3","module_4"])
async def test_module_receives_guidance_after_retrieval(context, provider, module):
    class KB:
        async def search(self, **kwargs):
            return CHUNKS
    provider.route_result = json.dumps({"selected_ids":["one"],"guidance":"GUIDANCE_SENTINEL：不要保证立即改善。","cautions":["不编造事实"]})
    context = dataclasses.replace(context,knowledge_base=KB(),prompt_snapshot={"knowledge_mediator":"CUSTOM_MEDIATOR"})
    result = await get_graph().ainvoke({"user_input":"行动和情绪有什么关系？","forced_module":module},context=context)
    assert result["telemetry"]["knowledge_mediator"]["status"] == "completed"
    assert "GUIDANCE_SENTINEL" in as_text(provider.systems[-1])
    assert "记录活动和感受。" not in as_text(provider.systems[-1])
    assert any("CUSTOM_MEDIATOR" in p for p in provider.route_systems)
    payload = json.loads(provider.route_calls[-1])
    assert payload["module"] == module and len(payload["knowledge"]) == 2


@pytest.mark.parametrize("raw", ["", "not json", '{"selected_ids":["invented"],"guidance":"ok"}', '{"selected_ids":[],"guidance":""}'])
async def test_bad_guidance_falls_back(settings,provider,raw):
    provider.route_result = raw
    chunks, block, metrics = await mediate_knowledge(state={"user_input":"test"},module="module_1",knowledge=CHUNKS,provider=provider,settings=settings)
    assert chunks == [] and block == "" and metrics["status"] == "fallback"


async def test_empty_evidence_skips_and_explicit_rejection_works(settings,provider):
    _, _, metrics = await mediate_knowledge(state={"user_input":"hi"},module="module_1",knowledge=[],provider=provider,settings=settings)
    assert metrics["status"] == "skipped" and not provider.route_calls
    provider.route_result = '{"selected_ids":[],"guidance":"材料不适用，先澄清问题。"}'
    chunks, block, metrics = await mediate_knowledge(state={"user_input":"hi"},module="module_1",knowledge=CHUNKS,provider=provider,settings=settings)
    assert chunks == [] and block and metrics["reason"] == "no_applicable_evidence"


async def test_timeout_and_disable(settings,provider):
    async def timeout(**kwargs):
        raise TimeoutError
    provider.route_detailed = timeout
    chunks, _, metrics = await mediate_knowledge(state={"user_input":"hi"},module="module_1",knowledge=CHUNKS,provider=provider,settings=settings)
    assert chunks == [] and metrics["reason"] == "timeout"
    settings.knowledge_mediator_enabled = False
    chunks, _, metrics = await mediate_knowledge(state={"user_input":"hi"},module="module_1",knowledge=CHUNKS,provider=provider,settings=settings)
    assert chunks == [] and metrics["reason"] == "disabled"


async def test_mediator_uses_bounded_fast_budget_and_top_candidates(settings, provider):
    """The selector must stay a short non-thinking call, not a second coach."""
    calls = {}

    async def complete(**kwargs):
        calls.update(kwargs)
        return type("Completion", (), {
            "text": '{"selected_ids":["chunk-0"],"guidance":"只作背景参考。"}',
            "model": "stub-mediator", "usage": {}, "reasoning_content": "unexpected",
            "finish_reason": "stop",
        })()

    provider.route_detailed = complete
    many_chunks = [
        KnowledgeChunk(id=f"chunk-{i}", text="内容" * 2000, source="test")
        for i in range(12)
    ]
    selected, _, metrics = await mediate_knowledge(
        state={"user_input": "test"}, module="module_1", knowledge=many_chunks,
        provider=provider, settings=settings,
    )
    assert [chunk.id for chunk in selected] == ["chunk-0"]
    assert calls["include_reasoning"] is False
    assert calls["reasoning_effort"] is None
    assert calls["max_tokens"] == 512
    payload = json.loads(calls["user"])
    assert len(payload["knowledge"]) <= 8
    assert all(len(chunk["text"]) <= 2200 for chunk in payload["knowledge"])
    assert metrics["candidate_chunk_count"] == len(payload["knowledge"])
    assert metrics["max_tokens"] == 512
    assert metrics["include_reasoning"] is False
    assert metrics["finish_reason"] == "stop"
    assert metrics["native_reasoning_present"] is True  # provider stub only; text is not exposed


async def test_truncated_mediator_never_releases_retrieved_chunks(settings, provider):
    """A length stop is a safety failure, not a partial approval."""
    async def truncated(**kwargs):
        return type("Completion", (), {
            "text": '{"selected_ids":["one"]', "model": "stub-mediator",
            "usage": {"output_tokens": 512}, "reasoning_content": "",
            "finish_reason": "length",
        })()

    provider.route_detailed = truncated
    selected, block, metrics = await mediate_knowledge(
        state={"user_input": "test"}, module="module_1", knowledge=CHUNKS,
        provider=provider, settings=settings,
    )
    assert selected == [] and block == ""
    assert metrics["status"] == "fallback"
    assert metrics["reason"] == "output_truncated"
    assert metrics["withheld_on_error"] is True


def test_prompt_editor_supports_mediator(client,admin_headers):
    path = "/api/admin/prompts/knowledge_mediator"
    response = client.put(path,headers=admin_headers,json={"content":"只指导知识的使用，不替代模块回答。"})
    assert response.status_code == 200 and response.json()["is_overridden"] is True
    bundle = client.get("/api/admin/prompts",headers=admin_headers).json()["prompts"]
    assert next(p for p in bundle if p["key"]=="knowledge_mediator")["content"] == response.json()["content"]
    assert client.delete(path,headers=admin_headers).status_code == 200
