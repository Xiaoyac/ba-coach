"""Gate boundaries and real module integration (including no extra provider calls)."""
from dataclasses import replace
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.graph.nodes import MODULE_NODES
from app.providers.base import as_text
from app.retrieval import KnowledgeChunk
from app.retrieval_intent import decide_retrieval
from app.schemas import Message

CASES = json.loads((Path(__file__).parent / "fixtures/retrieval_intent_cases.json").read_text(encoding="utf-8"))["cases"]


def state_for(case):
    return {"user_input": case["query"], "chat_history": [Message(**m) for m in case.get("history", [])],
            "memory": {**case.get("memory", {}), **({"pa_card": case["pa_card"]} if case.get("pa_card") else {})},
            "clinical_context": case.get("clinical_context", [])}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_behavior_contract(case):
    assert decide_retrieval(state_for(case)).retrieve is case["retrieve"]


@pytest.mark.parametrize("module", ["module_1", "module_2", "module_3", "module_4"])
@pytest.mark.asyncio
async def test_skip_avoids_search_keeps_reply_and_redacts_log(module, context, provider, caplog):
    kb = SimpleNamespace(search=AsyncMock(side_effect=AssertionError("search should be skipped")))
    context = replace(context, knowledge_base=kb)
    with caplog.at_level(logging.INFO, logger="app.graph.nodes"):
        result = await MODULE_NODES[module]({"user_input": "你好", "memory": {"secret": "PRIVATE_SENTINEL"}}, SimpleNamespace(context=context))
    kb.search.assert_not_awaited()
    assert len(provider.seen) == 1
    assert not provider.classify_calls and not provider.route_calls
    assert result["final_response"]
    assert "# Retrieved Knowledge" not in as_text(provider.systems[-1])
    assert result["telemetry"]["retrieval"]["outcome"] == "skipped"
    record = json.loads(next(r.message.split("retrieval_gate ", 1)[1] for r in caplog.records if "retrieval_gate " in r.message))
    assert record["gate"]["reason"] == "greeting"
    assert "PRIVATE_SENTINEL" not in caplog.text and "你好" not in caplog.text


@pytest.mark.asyncio
async def test_contextual_ack_passes_original_query_and_reference(context, provider, approved_mediator):
    chunk = KnowledgeChunk("kb:1", "REFERENCE_SENTINEL", "BA", 2.0)
    kb = SimpleNamespace(search=AsyncMock(return_value=[chunk]))
    state = {"user_input": "好的", "chat_history": [Message(role="user", content="如何使用两分钟规则")],
             "memory": {"pa_card": "晚饭后散步"}}
    result = await MODULE_NODES["module_4"](state, SimpleNamespace(context=replace(context, knowledge_base=kb)))
    kb.search.assert_awaited_once_with(module="module_4", query="好的\n如何使用两分钟规则\n晚饭后散步", top_k=2)
    assert "REFERENCE_SENTINEL" in as_text(provider.systems[-1])
    assert result["telemetry"]["retrieval"]["gate"]["reason"] == "contextual_acknowledgement"


@pytest.mark.asyncio
async def test_disabled_switch_restores_search_and_sse_telemetry(context, provider, monkeypatch):
    from app.graph import nodes
    events = []
    monkeypatch.setattr(nodes, "_emit", events.append)
    kb = SimpleNamespace(search=AsyncMock(return_value=[]))
    settings = context.settings.model_copy(update={"knowledge_intent_gate_enabled": False})
    result = await MODULE_NODES["module_2"]({"user_input": "收到"}, SimpleNamespace(
        context=replace(context, knowledge_base=kb, settings=settings, stream=True)))
    kb.search.assert_awaited_once()
    assert result["final_response"] == "hello"
    assert any(e.get("detail", {}).get("intent_gate", {}).get("gate", {}).get("reason") == "disabled" for e in events)


@pytest.mark.asyncio
async def test_search_error_is_not_reported_as_valid_empty(context, caplog):
    kb = SimpleNamespace(search=AsyncMock(side_effect=RuntimeError("test failure")))
    with caplog.at_level(logging.INFO, logger="app.graph.nodes"), pytest.raises(RuntimeError):
        await MODULE_NODES["module_2"]({"user_input": "如何制定活动计划"}, SimpleNamespace(context=replace(context, knowledge_base=kb)))
    assert '"outcome": "error"' in caplog.text


def test_env_switch(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("KNOWLEDGE_INTENT_GATE_ENABLED", "false")
    assert not Settings(_env_file=None).knowledge_intent_gate_enabled


def test_empty_progress_and_technical_counters_are_not_business_context():
    assert not decide_retrieval({"user_input": "收到", "memory": {"turn_count": "2", "last_module": "module_1"},
                                 "module_steps": {"module_1": [], "module_2": []}}).retrieve
    assert decide_retrieval({"user_input": "收到", "module_steps": {"module_1": ["ba_understanding"]}}).retrieve


def test_log_summary_separates_skips_empty_and_errors():
    from scripts.evaluate_intent_gate import aggregate_logs
    sample = {"gate": {"retrieve": True, "reason": "substantive_or_unknown", "version": "rules-v1"},
              "module": "module_2", "retriever": "DatabaseKnowledgeBase", "outcome": "empty",
              "gate_duration_ms": .01, "search_duration_ms": 20, "total_duration_ms": 20.01,
              "returned": 0, "context_chars": 0}
    records = [sample, {**sample, "outcome": "error"},
               {**sample, "gate": {**sample["gate"], "retrieve": False, "reason": "greeting"},
                "outcome": "skipped", "search_duration_ms": 0, "total_duration_ms": .01}]
    result = aggregate_logs(["INFO retrieval_gate " + json.dumps(r) for r in records] +
                            ["unrelated log", "INFO retrieval_gate {broken json"])
    group = next(iter(result["groups"].values()))
    assert result["events"] == 3 and result["invalid_events"] == 1
    assert group["skip_rate"] == pytest.approx(1 / 3)
    assert group["search_calls"] == 2 and group["error_rate_per_search"] == .5
    assert group["search_empty_rate"] == .5
    assert "correct_empty" not in group


def test_gate_eval_detects_false_skip_and_does_not_call_search_for_skipped():
    from scripts.evaluate_intent_gate import evaluate_group
    from app.retrieval import index_chunk
    corpus = (index_chunk(id=1, source_id=1, source_name="BA", category="BA", heading="你好", content="你好"),)
    calls = []
    def search(mode, module, query, k):
        calls.append(query)
        return [KnowledgeChunk("kb:1", "你好", "BA", 1.0)]
    # Deliberately label this greeting as needing knowledge: evaluator must expose it.
    report = evaluate_group([{"id": "test", "query": "你好", "module": "module_1", "kind": "test",
                              "expect_empty": False, "relevant": [{"source": "BA"}]}],
                            corpus, search, ["p0"], 1)
    assert calls == ["你好"]
    assert report["gate"]["false_skip_rate"] == 1
    assert not report["checks"]["no_positive_skips"]
