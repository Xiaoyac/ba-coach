from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.knowledge_mediator import mediate_knowledge
from app.providers.base import Completion
from app.providers.deepseek import DeepSeekProvider
from app.providers.doubao import DoubaoProvider
from app.retrieval import KnowledgeChunk


@pytest.mark.parametrize("provider_type", [DeepSeekProvider, DoubaoProvider])
@pytest.mark.parametrize("include_reasoning", [False, True])
async def test_native_reasoning_is_opt_in_single_request(provider_type, include_reasoning):
    provider = object.__new__(provider_type)
    provider._settings = SimpleNamespace(deepseek_router_model="test-model", doubao_router_model="test-model")
    response = SimpleNamespace(model="test-model", usage=None, choices=[SimpleNamespace(
        message=SimpleNamespace(content='{}', reasoning_content="native thought"), finish_reason="stop")])
    create = AsyncMock(return_value=response)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = await provider.route_detailed(system="contract", user="synthetic", max_tokens=1200, include_reasoning=include_reasoning)
    assert create.await_count == 1
    assert create.call_args.kwargs["extra_body"]["thinking"]["type"] == ("enabled" if include_reasoning else "disabled")
    assert result.reasoning_content == ("native thought" if include_reasoning else "")


@pytest.mark.parametrize("mode", ["normal", "empty_reasoning", "invalid", "timeout", "no_chunks"])
async def test_reasoning_is_separate_and_not_synthesized(context, provider, mode):
    async def complete(**kwargs):
        # Production mediator calls are deliberately non-thinking.  A stub
        # that returns a reasoning field anyway must not leak it to the UI.
        assert kwargs["include_reasoning"] is False
        assert kwargs["reasoning_effort"] is None
        assert kwargs["max_tokens"] == 512
        if mode == "timeout":
            raise TimeoutError
        return Completion(text="invalid" if mode == "invalid" else '{"decision":"use","selections":[{"id":"one","quote":"evidence","application":"GUIDANCE_ONLY"}],"note":""}',
            model="mediator-model", reasoning_content="" if mode == "empty_reasoning" else "THOUGHT_ONLY")
    provider.route_detailed = complete
    output = {"reasoning_content":"stale"}
    chunks = [] if mode == "no_chunks" else [KnowledgeChunk("one", "evidence", "synthetic")]
    selected, block, metrics = await mediate_knowledge(state={"user_input":"synthetic"},module="module_2",
        knowledge=chunks,provider=provider,settings=context.settings,debug_output=output)
    assert "THOUGHT_ONLY" not in str(metrics) and "THOUGHT_ONLY" not in block
    assert output.get("reasoning_content") is None
    if mode in ("invalid", "timeout", "no_chunks"):
        assert selected == [] and block == ""


async def test_real_wait_for_timeout_cancels_request(context):
    import asyncio
    cancelled = asyncio.Event()
    calls = 0
    class SlowProvider:
        async def route_detailed(self, **kwargs):
            nonlocal calls
            calls += 1
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.set()
    settings = SimpleNamespace(knowledge_mediator_enabled=True,knowledge_mediator_timeout_seconds=.01)
    selected, block, metrics = await mediate_knowledge(state={"user_input":"synthetic"},module="module_2",
        knowledge=[KnowledgeChunk("one","evidence","test")],provider=SlowProvider(),settings=settings)
    assert metrics["reason"] == "timeout" and metrics["withheld_on_error"]
    assert selected == [] and block == "" and calls == 1 and cancelled.is_set()


@pytest.mark.parametrize("provider_type", [DeepSeekProvider, DoubaoProvider])
async def test_low_effort_only_applies_to_explicit_mediator_call(provider_type):
    provider = object.__new__(provider_type)
    provider._settings = SimpleNamespace(deepseek_router_model="test",doubao_router_model="test")
    response = SimpleNamespace(model="test",usage=None,choices=[SimpleNamespace(
        message=SimpleNamespace(content='{}',reasoning_content="native"),finish_reason="stop")])
    create = AsyncMock(return_value=response)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider._client.with_options = Mock(return_value=provider._client)
    await provider.route_detailed(system="test",user="test",max_tokens=1200,include_reasoning=True,reasoning_effort="low")
    assert create.call_args.kwargs["reasoning_effort"] == "low"
    provider._client.with_options.assert_called_once_with(max_retries=0)
    await provider.route_with_reasoning(system="test",user="test",max_tokens=1200)
    assert "reasoning_effort" not in create.call_args.kwargs


@pytest.mark.parametrize("text,finish,reason", [
    ('', 'stop', 'empty_output'), ('not-json', 'stop', 'invalid_json'),
    ('{}','stop','invalid_schema'),
    ('{"decision":"use","selections":[{"id":"unknown","quote":"evidence","application":"ok"}],"note":""}', 'stop','invalid_evidence'),
    ('{"selected_ids":["one"],"guidance":"ok"}', 'length','output_truncated'),
])
async def test_invalid_results_are_classified_and_never_passed(context, provider, text, finish, reason):
    async def complete(**kwargs):
        return Completion(text=text,model="test",finish_reason=finish)
    provider.route_detailed = complete
    selected, block, metrics = await mediate_knowledge(state={"user_input":"synthetic"},module="module_2",
        knowledge=[KnowledgeChunk("one","evidence","test")],provider=provider,settings=context.settings)
    assert metrics["reason"] == reason and selected == [] and block == ""


async def test_compact_input_preserves_full_safety_facts(context, provider):
    import json
    facts=[{"text":"运动禁忌：不能负重", "confirmation":"confirmed", "source_kind":"user", "source_id":"test"}]
    async def complete(**kwargs):
        payload=json.loads(kwargs["user"])
        assert payload["facts"] == facts
        assert kwargs["user"] == json.dumps(payload,ensure_ascii=False,separators=(",",":"))
        assert kwargs["reasoning_effort"] is None
        assert kwargs["include_reasoning"] is False
        return Completion(text='{"decision":"not_needed","selections":[],"note":"当前无需补充知识"}',model="test")
    provider.route_detailed=complete
    state={"user_input":"synthetic","knowledge_context":{"facts":facts}}
    _,_,metrics=await mediate_knowledge(state=state,module="module_2",knowledge=[KnowledgeChunk("one","evidence","test")],provider=provider,settings=context.settings)
    assert metrics["status"] == "completed" and metrics["timeout_seconds"] == 8
    assert metrics["configured_timeout_seconds"] == 8
    assert metrics["max_tokens"] == 512
    facts[0]["text"]="约束"*30000
    _,_,metrics=await mediate_knowledge(state=state,module="module_2",knowledge=[KnowledgeChunk("one","evidence","test")],provider=provider,settings=context.settings)
    assert metrics["reason"] == "context_too_large"
