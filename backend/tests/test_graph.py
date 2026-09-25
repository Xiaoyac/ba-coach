"""Graph-level tests: DAG shape, node behaviour, state transitions.

These drive the compiled graph directly with a `GraphContext`, so they cover
orchestration without going through HTTP.
"""

from __future__ import annotations

import asyncio
import dataclasses
import pytest

from app.graph import build_graph, get_graph
from app.graph import nodes as nodes_module
from app.graph.nodes import MODULE_CONFIGS
from app.prompts import (
    GLOBAL_PROMPT,
    MODULE_PROMPTS,
    SystemPromptSegment,
    append_admin_prompt_overrides,
    build_system_segments,
)
from app.providers.base import Completion, ProviderError, as_segments, as_text
from app.retrieval import StubKnowledgeBase
from app.schemas import Message


async def _drain_background_tasks() -> None:
    """Wait out whatever summarizer_node fired off, so assertions can see it.

    summarizer_node deliberately does not await its own work (see nodes.py) —
    tests need to explicitly wait for it instead of relying on incidental
    scheduling.
    """
    pending = list(nodes_module._background_tasks)
    if pending:
        await asyncio.gather(*pending)

async def run(state: dict, context) -> dict:
    """Run the response graph, then settle post-reply fact bookkeeping."""
    final = await get_graph().ainvoke(state, context=context)
    nodes_module.schedule_background_routing(
        final, context, assistant_message_id=None
    )
    await nodes_module.wait_for_pending_routing(final.get("session_id"))
    session = await context.store.get(final["session_id"])
    if session is not None:
        final["next_module"] = session.module
    return final


async def test_module_router_uses_dedicated_provider(context, provider) -> None:
    router_provider = type(provider)()
    router_provider.route_result = '{"target_module":"1"}'
    context = dataclasses.replace(context, router_provider=router_provider)
    await run({"user_input": "你好", "metadata": {}}, context)
    assert sum("target_module" in system for system in router_provider.route_systems) == 1
    assert any("风险信号检测器" in system for system in router_provider.route_systems)
    assert not any("target_module" in system for system in provider.route_systems)
    assert provider.seen


async def test_router_finishes_before_reply_generation(context, provider) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    original = context.router_provider.route_with_reasoning
    async def gated_router(**kwargs) -> Completion:
        if "target_module" not in kwargs["system"]:
            return await original(**kwargs)
        started.set()
        await release.wait()
        return Completion(text='{"target_module":"1"}', model="stub-router-1", reasoning_content="前置判断完成")
    context.router_provider.route_with_reasoning = gated_router
    task = asyncio.create_task(get_graph().ainvoke({"user_input":"你好","metadata":{}}, context=context))
    await asyncio.wait_for(started.wait(), 2)
    assert provider.seen == [], "reply cannot precede its Router decision"
    release.set()
    final = await asyncio.wait_for(task, 2)
    assert final["final_response"]
    assert final["routing_reasoning_content"].endswith("前置判断完成")
    count = len(provider.route_systems)
    nodes_module.schedule_background_routing(final, context, assistant_message_id=None)
    await nodes_module.wait_for_pending_routing(final["session_id"])
    assert len(provider.route_systems) == count, "no second Router after reply"


async def test_turn_records_stage_telemetry(context) -> None:
    final = await run(
        {"user_input": "你好", "forced_module": "module_1", "metadata": {}},
        context,
    )
    telemetry = final["telemetry"]
    assert telemetry["risk_gate_duration_ms"] >= 0
    assert telemetry["main_generation_duration_ms"] >= 0
    assert telemetry["time_to_first_content_token_ms"] >= 0
    assert len(telemetry["prompt_version"]) == 16
    assert telemetry["usage"] == {"input_tokens": 1, "output_tokens": 1}


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_graph_has_expected_nodes() -> None:
    graph = get_graph().get_graph()
    assert {node.id for node in graph.nodes.values()} == {
        "__start__",
        "extract_memory",
        "analyze_intent",
        "recall_memory",
        "risk_gate",
        "pre_reply_router",
        "crisis",
        "module_1",
        "module_2",
        "module_3",
        "module_4",
        "route_next_module",
        "summarizer",
        "update_memory_and_format",
        "__end__",
    }


def test_every_module_branches_from_intent_and_converges() -> None:
    edges = [(e.source, e.target, e.conditional) for e in get_graph().get_graph().edges]
    for module in MODULE_PROMPTS:
        assert ("pre_reply_router", module, True) in edges, f"{module} not routed to"
        assert (module, "route_next_module", False) in edges, (
            f"{module} does not converge on route_next_module"
        )
    # The crisis branch is reachable from the same gate and converges too.
    assert ("risk_gate", "crisis", True) in edges
    assert ("crisis", "route_next_module", False) in edges
    assert ("analyze_intent", "recall_memory", False) in edges
    assert ("recall_memory", "risk_gate", False) in edges
    assert ("route_next_module", "summarizer", False) in edges
    assert ("summarizer", "update_memory_and_format", False) in edges
    assert ("update_memory_and_format", "__end__", False) in edges


def test_graph_compiles_from_a_fresh_builder() -> None:
    # Guards against the lru_cache masking a wiring error introduced later.
    assert build_graph().compile() is not None


def test_every_module_has_a_config() -> None:
    assert set(MODULE_CONFIGS) == set(MODULE_PROMPTS)


# ---------------------------------------------------------------------------
# Routing: analyze_intent_node reads state, route_next_module_node decides it
# ---------------------------------------------------------------------------


async def test_explicit_module_bypasses_state(context, provider) -> None:
    final = await run(
        {"user_input": "怎么办", "forced_module": "module_4", "metadata": {}}, context
    )
    assert final["extracted_intent"] == "module_4"
    assert final["routed_by"] == "explicit"


async def test_fresh_session_defaults_to_module_1(context, provider) -> None:
    final = await run({"user_input": "你好", "metadata": {}}, context)
    assert final["extracted_intent"] == "module_1"
    assert final["routed_by"] == "pre_reply_router"


async def test_current_module_is_read_back_as_sticky(context, store, provider) -> None:
    # Seed the session's module directly — analyze_intent_node's only job is
    # to read this back, not to decide it.
    session = await store.get_or_create(None)
    await store.set_module(session.session_id, "module_3")

    final = await run(
        {"user_input": "嗯", "session_id": session.session_id, "metadata": {}}, context
    )
    assert final["extracted_intent"] == "module_3"
    assert final["routed_by"] == "pre_reply_router"


async def test_system_prompt_carries_authoritative_reply_module(context, provider) -> None:
    await get_graph().ainvoke(
        {"user_input": "继续聊刚才的事", "forced_module": "module_2", "metadata": {}},
        context=context,
    )
    system = as_text(provider.systems[-1])
    assert "# 本轮运行事实" in system
    assert "current_module: module_2" in system
    assert "reply_module:" not in system
    assert "生成文字本身不执行保存" in system


async def test_direct_module_question_is_answered_from_state_not_model(
    context, provider
) -> None:
    final = await get_graph().ainvoke(
        {"user_input": "现在是模块几？", "forced_module": "module_3", "metadata": {}},
        context=context,
    )
    assert final["extracted_intent"] == "module_3"
    assert "module_3" not in final["final_response"]
    assert provider.seen, "ordinary reply must not leak internal numbering"


async def test_background_router_receives_prior_module_history(
    context, store, provider
) -> None:
    session = await store.get_or_create(None)
    await store.set_module(session.session_id, "module_1")
    await store.append(
        session.session_id,
        Message(role="user", content="较早轮次里我讲过一次具体的加班事件"),
    )
    await store.append(
        session.session_id,
        Message(role="assistant", content="我们已经结合这件事解释过行为和情绪循环"),
    )
    provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'

    await run(
        {
            "user_input": "我理解了，也愿意开始目标设定",
            "session_id": session.session_id,
            "metadata": {},
        },
        context,
    )

    router_input = next(
        call for call in provider.route_calls if "本 Session 对话记录" in call
    )
    assert "较早轮次里我讲过一次具体的加班事件" in router_input
    assert "已经结合这件事解释过行为和情绪循环" in router_input


async def test_route_next_module_persists_the_agents_decision(context, store, provider) -> None:
    provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'
    first = await run(
        {"user_input": "我准备好了", "metadata": {}}, context
    )
    assert first["next_module"] == "module_2"

    session = await store.get(first["session_id"])
    assert session.module == "module_2"

    # The next turn reads that back with no explicit pin.
    second = await run(
        {"user_input": "继续", "session_id": first["session_id"], "metadata": {}}, context
    )
    assert second["extracted_intent"] == "module_2"
    assert second["routed_by"] == "pre_reply_router"


async def test_route_accepts_a_bare_module_without_microsteps(context, store, provider) -> None:
    provider.route_result = "2"
    final = await run(
        {"user_input": "我准备好了", "metadata": {}},
        context,
    )
    session = await store.get(final["session_id"])
    assert session is not None
    assert session.module == "module_2"


async def test_route_next_module_rejects_a_skipped_step(context, provider, store) -> None:
    # The rules only allow 1 -> 2 from module 1; 1 -> 3 skips goal-setting
    # entirely and must be rejected even though the model proposed it.
    provider.route_result = '{"target_module":"3","completed_steps":["pa_concept_understood","values_or_intention_explored","activity_selected","pa_card_completed"]}'
    final = await run(
        {"user_input": "随便说点什么", "metadata": {}}, context
    )
    assert final["next_module"] == "module_1"


async def test_route_next_module_requires_a_pa_card_for_module_3(context, provider, store) -> None:
    provider.route_result = '{"target_module":"3","completed_steps":["pa_concept_understood","values_or_intention_explored","activity_selected","pa_card_completed"]}'
    session = await store.get_or_create(None)
    await store.set_module(session.session_id, "module_2")
    final = await run(
        {"user_input": "开始执行", "session_id": session.session_id, "metadata": {}}, context
    )
    # No PA card exists yet (fresh session, stub reply carries no card), so
    # the transition is rejected regardless of what the model proposed.
    assert final["next_module"] == "module_2"


async def test_route_next_module_allows_module_3_once_a_pa_card_exists(
    context, store, provider
) -> None:
    session = await store.get_or_create(None)
    await store.set_module(session.session_id, "module_2")
    await store.set_memory(session.session_id, {"pa_card": "当前PA目标\n• 活动内容：散步"})
    provider.route_result = '{"target_module":"3","completed_steps":["pa_concept_understood","values_or_intention_explored","activity_selected","pa_card_completed"]}'

    final = await run(
        {
            "user_input": "开始执行",
            "session_id": session.session_id,
            "metadata": {},
        },
        context,
    )
    assert final["next_module"] == "module_3"


async def test_route_next_module_garbage_falls_back_to_current(context, provider, store) -> None:
    provider.route_result = "not json"
    session = await store.get_or_create(None)
    await store.set_module(session.session_id, "module_2")
    final = await run(
        {"user_input": "hi", "session_id": session.session_id, "metadata": {}}, context
    )
    assert final["next_module"] == "module_2"


async def test_route_next_module_skipped_on_provider_error(context, provider) -> None:
    provider.fail_with = ProviderError("upstream exploded")
    provider.route_result = '{"target_module": "2"}'
    final = await run(
        {"user_input": "hi", "forced_module": "module_1", "metadata": {}}, context
    )
    # Nothing worth reasoning about happened this turn — hold position rather
    # than spend a second model call on a turn that already failed. The risk
    # gate's own call still happened (it runs before the module), so this
    # asserts the *router* was skipped rather than that nothing ran.
    assert final["next_module"] == "module_1"
    assert not any("target_module" in sys for sys in provider.route_systems)


# ---------------------------------------------------------------------------
# Pre-processing / state
# ---------------------------------------------------------------------------


async def test_extract_memory_loads_history_for_later_turns(context, provider) -> None:
    first = await run({"user_input": "one", "metadata": {}}, context)
    session_id = first["session_id"]
    await run({"user_input": "two", "session_id": session_id, "metadata": {}}, context)

    # Turn 2's model call sees: user "one", assistant reply, user "two".
    assert [m.content for m in provider.seen[-1]] == ["one", "saw 1 messages", "<user_message>two</user_message>"]


async def test_state_carries_the_documented_fields(context) -> None:
    final = await run({"user_input": "我该怎么办？", "metadata": {"locale": "zh-CN"}}, context)
    for field in (
        "user_input",
        "chat_history",
        "current_module",
        "extracted_intent",
        "retrieved_knowledge",
        "next_module",
        "final_response",
    ):
        assert field in final, f"{field} missing from final state"


# ---------------------------------------------------------------------------
# Long-term memory: recall_memory_node + summarizer_node
# ---------------------------------------------------------------------------


async def test_recall_skipped_without_subject_id(context, memos) -> None:
    context = dataclasses.replace(context, memos=memos)
    final = await run({"user_input": "你好", "metadata": {}}, context)
    assert final["long_term_memory"] == []
    assert memos.retrieve_calls == []


async def test_recall_skipped_without_memos_configured(context) -> None:
    # context.memos is None by default (Memos not configured) — even with a
    # subject_id, there is nothing to recall from.
    final = await run(
        {"user_input": "你好", "subject_id": "subj-1", "metadata": {}}, context
    )
    assert final["long_term_memory"] == []


async def test_recall_fires_on_first_turn(context, provider, memos) -> None:
    context = dataclasses.replace(context, memos=memos)
    memos.retrieve_result = ["上次聊到用户的困扰是失眠"]
    final = await run(
        {"user_input": "你好", "subject_id": "subj-1", "metadata": {}}, context
    )
    assert memos.retrieve_calls == ["subj-1"]
    assert final["long_term_memory"] == ["上次聊到用户的困扰是失眠"]
    # And it actually lands in the module's prompt, not just the state.
    system = as_text(provider.systems[-1])
    assert "长期记忆" in system
    assert "上次聊到用户的困扰是失眠" in system


async def test_recall_does_not_fire_on_an_ordinary_later_turn(context, store, memos) -> None:
    context = dataclasses.replace(context, memos=memos)
    first = await run(
        {"user_input": "你好", "subject_id": "subj-1", "forced_module": "module_1", "metadata": {}},
        context,
    )
    memos.retrieve_calls.clear()

    await run(
        {
            "user_input": "继续",
            "session_id": first["session_id"],
            "subject_id": "subj-1",
            "forced_module": "module_1",
            "metadata": {},
        },
        context,
    )
    assert memos.retrieve_calls == []


async def test_recall_fires_on_first_entry_into_module_4(context, store, memos) -> None:
    context = dataclasses.replace(context, memos=memos)
    session = await store.get_or_create(None)
    # Simulate a session that has been through module_3 already — chat_history
    # non-empty, last_module = module_3 — then this turn lands in module_4.
    await store.set_memory(session.session_id, {"last_module": "module_3"})
    await store.append(session.session_id, Message(role="user", content="之前的话"))

    await run(
        {
            "user_input": "我完成了",
            "session_id": session.session_id,
            "subject_id": "subj-1",
            "forced_module": "module_4",
            "metadata": {},
        },
        context,
    )
    assert memos.retrieve_calls == ["subj-1"]


async def test_summarizer_dispatches_on_module_change(context, memos) -> None:
    context = dataclasses.replace(context, memos=memos)
    context.provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'
    final = await run(
        {
            "user_input": "我准备好了",
            "subject_id": "subj-1",
            "metadata": {},
        },
        context,
    )
    assert final["next_module"] == "module_2"
    await _drain_background_tasks()
    assert len(memos.saved) == 1
    saved_subject, summary_text = memos.saved[0]
    assert saved_subject == "subj-1"
    assert summary_text  # the stub route() echoes something non-empty


async def test_summarizer_does_not_block_the_response(context, provider, memos) -> None:
    """The graph must return before the summary is written, not after.

    This is the actual requirement — a summary is a side effect that happens
    to a conversation, never something the person waiting on a reply pays
    latency for. `save_memo` is deliberately gated on an event this test
    controls: if summarizer_node awaited its own work, `ainvoke` would
    deadlock here rather than fail an assertion.
    """
    release = asyncio.Event()
    saved_before_release: list[bool] = []

    async def gated_save_memo(user_id: str, summary_text: str) -> bool:
        await release.wait()
        memos.saved.append((user_id, summary_text))
        return True

    memos.save_memo = gated_save_memo
    context = dataclasses.replace(context, memos=memos)
    provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'

    final = await run(
        {
            "user_input": "我准备好了",
            "subject_id": "subj-1",
            "metadata": {},
        },
        context,
    )

    # The turn is fully done and its reply is available while save_memo is
    # still parked inside the background task.
    assert final["final_response"]
    saved_before_release.append(bool(memos.saved))

    release.set()
    await _drain_background_tasks()

    assert saved_before_release == [False], "summarizer blocked the graph"
    assert len(memos.saved) == 1


async def test_summarizer_failure_does_not_affect_the_turn(context, provider, memos) -> None:
    """A broken summarizer is a log line, not a failed conversation."""

    async def exploding_save_memo(user_id: str, summary_text: str) -> bool:
        raise RuntimeError("memos is down")

    memos.save_memo = exploding_save_memo
    context = dataclasses.replace(context, memos=memos)
    provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'

    final = await run(
        {
            "user_input": "我准备好了",
            "subject_id": "subj-1",
            "metadata": {},
        },
        context,
    )
    # _summarize_and_save swallows it, so gathering the task must not raise.
    await _drain_background_tasks()

    assert final["error"] is None
    assert final["final_response"]
    assert final["next_module"] == "module_2"


async def test_summarizer_skipped_when_module_does_not_change(context, memos) -> None:
    context = dataclasses.replace(context, memos=memos)
    await run(
        {
            "user_input": "嗯嗯",
            "subject_id": "subj-1",
            "metadata": {},
        },
        context,
    )
    await _drain_background_tasks()
    assert memos.saved == []


async def test_summarizer_skipped_without_subject_id(context, memos) -> None:
    context = dataclasses.replace(context, memos=memos)
    context.provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'
    await run(
        {"user_input": "我准备好了", "metadata": {}}, context
    )
    await _drain_background_tasks()
    assert memos.saved == []


async def test_router_and_summarizer_use_their_separate_token_budgets(
    context, provider, memos
) -> None:
    context = dataclasses.replace(context, memos=memos)
    provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'
    await run(
        {
            "user_input": "我准备好了",
            "subject_id": "subj-1",
            "metadata": {},
        },
        context,
    )
    await _drain_background_tasks()
    # Both calls use the cheap model, but only the module router enables and
    # displays reasoning. Keep its thought budget independent from the 512
    # token detached summary so changing one cannot silently affect the other.
    assert provider.route_max_tokens_calls[-2:] == [
        context.settings.router_reasoning_max_tokens,
        context.settings.summarizer_max_tokens,
    ]
    assert context.settings.router_reasoning_max_tokens == 1024
    assert context.settings.summarizer_max_tokens == 512


async def test_summarizer_prompt_is_not_the_router_prompt(context, memos) -> None:
    """The two jobs stay in separate prompts — see summarizer_node's docstring."""
    from app.graph.nodes import SUMMARIZER_PROMPT
    from app.router_agent import ROUTER_AGENT_PROMPT

    assert SUMMARIZER_PROMPT != ROUTER_AGENT_PROMPT
    assert "target_module" not in SUMMARIZER_PROMPT


# ---------------------------------------------------------------------------
# Module branches / retrieval
# ---------------------------------------------------------------------------


async def test_module_node_retrieves_knowledge(context, approved_mediator) -> None:
    final = await run(
        {"user_input": "grounding exercise", "forced_module": "module_3", "metadata": {}},
        context,
    )
    chunks = final["retrieved_knowledge"]
    assert chunks
    assert all(chunk.source == "module_3" for chunk in chunks)
    assert len(chunks) <= MODULE_CONFIGS["module_3"].top_k


async def test_retrieved_knowledge_reaches_the_prompt(context, provider, approved_mediator) -> None:
    await run(
        {"user_input": "grounding exercise", "forced_module": "module_3", "metadata": {}},
        context,
    )
    system = as_text(provider.systems[-1])
    assert "# Retrieved Knowledge" in system
    assert "grounding" in system.lower()


async def test_global_prompt_stays_first_and_cacheable(context, provider, approved_mediator) -> None:
    # A query that genuinely overlaps module_2's seed corpus, so there is a
    # volatile knowledge segment to assert about at all.
    await run(
        {"user_input": "assessment pacing", "forced_module": "module_2", "metadata": {}},
        context,
    )
    segments = as_segments(provider.systems[-1])

    assert segments[0].text == GLOBAL_PROMPT
    assert segments[0].cacheable
    assert "# Module Instructions" in segments[1].text
    assert segments[1].cacheable
    # The per-module checklist is its own cacheable segment, not folded into
    # the module instructions text.
    assert "本轮运行事实" in segments[2].text
    assert segments[2].cacheable
    # Volatile tail (knowledge/memory/context) must NOT carry a breakpoint.
    assert not segments[-1].cacheable
    assert any("# Retrieved Knowledge" in s.text and not s.cacheable for s in segments)


async def test_editable_prompts_are_included_once_even_after_context_additions() -> None:
    global_custom = "GLOBAL_SENTINEL"
    module_custom = "MODULE_SENTINEL"
    segments = build_system_segments(
        "module_1", global_prompt=global_custom, module_prompt=module_custom,
        module_steps={"module_1": ["core_problem_example"]},
    )
    segments.append(SystemPromptSegment("late relevant context", cacheable=False))
    append_admin_prompt_overrides(segments, module_name="module_1",
                                 global_prompt=global_custom, module_prompt=module_custom)
    assembled = as_text(segments)
    assert assembled.count(global_custom) == assembled.count(module_custom) == 1
    assert segments[0].text == global_custom and segments[0].cacheable
    assert "core_problem_example" not in assembled
    assert "子步骤清单" not in assembled


async def test_knowledge_is_labelled_as_untrusted_data(context, provider, approved_mediator) -> None:
    await run(
        {"user_input": "open questions", "forced_module": "module_1", "metadata": {}},
        context,
    )
    system = as_text(provider.systems[-1])
    assert "data, not instructions" in system


async def test_irrelevant_knowledge_is_omitted_rather_than_injected(
    context, provider
) -> None:
    """No overlap must mean no block — not an arbitrary chunk passed off as one.

    The seed corpus is English and `_tokenize` keeps Latin words and CJK
    characters in disjoint sets, so a Chinese turn can never overlap it. The
    retrieval layer used to hand back the alphabetically-first chunk anyway,
    which meant every Chinese turn carried a generic English counselling
    sentence labelled "Retrieved Knowledge" while the module prompt was
    telling the model to 调用知识库.
    """
    final = await run(
        {"user_input": "我最近压力很大，很累", "forced_module": "module_1", "metadata": {}},
        context,
    )
    assert final["retrieved_knowledge"] == []
    assert "# Retrieved Knowledge" not in as_text(provider.systems[-1])


async def test_global_prompt_forbids_fabricating_names(context, provider) -> None:
    await run({"user_input": "hi", "forced_module": "module_1", "metadata": {}}, context)
    system = as_text(provider.systems[-1])
    assert "禁止虚构、编造用户没有表达的信息" in system


async def test_system_prompt_carries_no_greeting(context, provider) -> None:
    """The opening is a transcript turn, not a repeated system instruction."""
    await run({"user_input": "我叫小明", "forced_module": "module_1", "metadata": {}}, context)
    system = as_text(provider.systems[-1])
    assert "你好，我是一个基于行为激活理论工作的 AI 教练" not in system
    assert "怎么称呼你" not in system


@pytest.mark.parametrize("module", ["module_1", "module_2", "module_3", "module_4"])
async def test_reply_has_no_duplicate_server_coaching_script(context, provider, module) -> None:
    await run({"user_input": "我叫小明", "forced_module": module}, context)
    system = as_text(provider.systems[-1])
    for legacy in ("# 本轮执行协议", "最早尚未完成", "子步骤清单", "# 模块一防循环与衔接规则",
                   "# 权威子步骤进度", "# 服务器强制"):
        assert legacy not in system
    assert "current_module: " + module in system


async def test_module_prompts_carry_no_dev_notes(context, provider) -> None:
    """Supervisor asides and open design questions are not instructions."""
    for module in ("module_2", "module_4"):
        await run({"user_input": "hi", "forced_module": module, "metadata": {}}, context)
        system = as_text(provider.systems[-1])
        assert "老师：" not in system, f"{module} still ships supervisor notes"
        assert "测试一下" not in system, f"{module} still ships an open design question"
        assert "备注：" not in system, f"{module} still ships dev notes"


async def test_only_selected_module_prompt_is_loaded(context, provider) -> None:
    await run({"user_input": "hi", "forced_module": "module_2", "metadata": {}}, context)
    system = as_text(provider.systems[-1])
    assert MODULE_PROMPTS["module_2"] in system
    assert "ABC 功能分析" not in system  # module_4's checklist, not module_2's


async def test_module_two_can_discuss_a_new_goal_before_one_is_bound(
    context, provider, monkeypatch
) -> None:
    """An unbound M2 chat is the Agent-led goal-clarification surface."""
    from app import v2_workflow

    class DummySession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    class DummyMaker:
        def __call__(self):
            return DummySession()

    async def unbound_runtime(_db, _session_id):
        return object(), {"active_goal_id": None, "flow_status": "active"}

    monkeypatch.setattr(v2_workflow, "runtime_for", unbound_runtime)
    context = dataclasses.replace(
        context,
        sessionmaker=DummyMaker(),
        settings=context.settings.model_copy(update={"database_schema_version": "v2"}),
    )
    final = await run(
        {"session_id": "new-goal-chat", "user_input": "我想找一个适合的新目标",
         "forced_module": "module_2", "metadata": {}},
        context,
    )
    assert final["final_response"] == "saw 1 messages"
    assert "请先" not in final["final_response"]


async def test_module_knowledge_rules_match_the_curated_source_map(
    context, provider
) -> None:
    await run({"user_input": "我想散步", "forced_module": "module_2", "metadata": {}}, context)
    module_two = as_text(provider.systems[-1])
    assert "PA 概念及相关专业知识" in module_two
    assert "不作为用户经历的证据" in module_two

    await run({"user_input": "我担心坚持不了", "forced_module": "module_3", "metadata": {}}, context)
    module_three = as_text(provider.systems[-1])
    assert "不在模块三内解决具体 PA 执行困难" in module_three
    assert "不在未了解用户具体顾虑前直接提供轻量化方案" in module_three


async def test_empty_knowledge_base_still_produces_a_prompt(context, provider) -> None:
    context = dataclasses.replace(context, knowledge_base=StubKnowledgeBase({}))
    final = await run({"user_input": "hi", "forced_module": "module_1", "metadata": {}}, context)
    assert final["retrieved_knowledge"] == []
    assert "# Retrieved Knowledge" not in as_text(provider.systems[-1])


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------


async def test_turn_is_persisted_by_the_post_node(context, store) -> None:
    final = await run({"user_input": "hi", "metadata": {}}, context)
    session = await store.get(final["session_id"])
    assert [m.content for m in session.messages] == ["hi", "saw 1 messages"]


async def test_memory_accumulates_across_turns(context, store) -> None:
    first = await run(
        {"user_input": "我该怎么办？", "forced_module": "module_3", "metadata": {}}, context
    )
    session_id = first["session_id"]
    assert first["memory"]["turn_count"] == "1"
    assert first["memory"]["last_module"] == "module_3"

    second = await run({"user_input": "再说说", "session_id": session_id, "metadata": {}}, context)
    assert second["memory"]["turn_count"] == "2"

    session = await store.get(session_id)
    assert session.memory["turn_count"] == "2"


async def test_memory_is_fed_back_into_the_prompt(context, provider) -> None:
    first = await run({"user_input": "我该怎么办？", "metadata": {}}, context)
    await run(
        {"user_input": "再说说", "session_id": first["session_id"], "metadata": {}}, context
    )
    system = as_text(provider.systems[-1])
    assert "# Recalled Context" in system
    assert "turn_count" in system


async def test_long_user_message_is_truncated_in_memory(context) -> None:
    final = await run({"user_input": "x" * 500, "metadata": {}}, context)
    assert len(final["memory"]["last_user_message"]) <= 200


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


async def test_provider_failure_converges_instead_of_raising(context, provider, store) -> None:
    provider.fail_with = ProviderError("upstream exploded")
    final = await run({"user_input": "hi", "metadata": {}}, context)

    # The graph still reached the post node.
    assert final["error"] == "upstream exploded"
    assert final["memory"]["turn_count"] == "1"

    # The user turn is recorded; no empty assistant turn was written.
    session = await store.get(final["session_id"])
    assert [m.content for m in session.messages] == ["hi"]


async def test_streamed_partial_is_kept_on_mid_stream_failure(context, provider, store) -> None:
    context = dataclasses.replace(context, stream=True)
    provider.fail_with = ProviderError("died mid-stream")
    final = await run({"user_input": "hi", "metadata": {}}, context)

    assert final["error"] == "died mid-stream"
    # First delta arrived before the failure and must not be lost.
    assert final["final_response"] == "he"
    session = await store.get(final["session_id"])
    assert [m.content for m in session.messages] == ["hi", "he"]


# ---------------------------------------------------------------------------
# Custom stream
# ---------------------------------------------------------------------------


async def test_custom_stream_event_order(context, provider) -> None:
    context = dataclasses.replace(context, stream=True)
    events = [
        event
        async for event in get_graph().astream(
            {"user_input": "我该怎么办？", "forced_module": "module_3", "metadata": {}},
            context=context,
            stream_mode="custom",
        )
    ]
    kinds = [event["type"] for event in events]

    assert kinds.index("meta") < kinds.index("delta")
    assert kinds.index("delta") < kinds.index("done")
    assert [e["text"] for e in events if e["type"] == "delta"] == ["he", "ll", "o"]

    done = next(e for e in events if e["type"] == "done")
    assert done["reply_module"] == "module_3"
    assert done["routed_by"] == "explicit"


async def test_custom_stream_emits_error_event(context, provider) -> None:
    context = dataclasses.replace(context, stream=True)
    provider.fail_with = ProviderError("boom")
    events = [
        event
        async for event in get_graph().astream(
            {"user_input": "hi", "metadata": {}}, context=context, stream_mode="custom"
        )
    ]
    error = next(e for e in events if e["type"] == "error")
    assert error["detail"] == "boom"
    assert not any(e["type"] == "done" for e in events)


# ---------------------------------------------------------------------------
# Clinical persistence dispatch
# ---------------------------------------------------------------------------


async def test_risk_is_screened_every_turn_not_only_on_a_transition(
    context, provider, db_sessionmaker
) -> None:
    """A risk signal does not wait for a module boundary."""
    context.sessionmaker = db_sessionmaker
    provider.route_result = ""  # router keeps the module the same

    await get_graph().ainvoke(
        {"session_id": "s", "user_input": "我今天很累", "subject_id": "subj-1"},
        context=context,
    )
    await _drain_background_tasks()

    # Which prompt was used identifies the caller; module extraction must not
    # have run, since the module did not change.
    assert any("风险信号检测器" in s for s in provider.route_systems)
    assert not any("信息抽取器" in s for s in provider.route_systems)


async def test_module_extraction_runs_on_a_transition(
    context, provider, db_sessionmaker
) -> None:
    from sqlalchemy import select

    from app.models_business import ModuleOneRecord

    context.sessionmaker = db_sessionmaker
    provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'  # the router moves us on
    provider.extraction_result = '{"chief_complaint": "最近睡不着"}'

    await run(
        {"session_id": "s", "user_input": "我想进入下一步", "subject_id": "subj-1"},
        context,
    )
    await _drain_background_tasks()

    async with db_sessionmaker() as db:
        rows = (await db.execute(select(ModuleOneRecord))).scalars().all()
    assert len(rows) == 1
    assert rows[0].chief_complaint == "最近睡不着"
    assert rows[0].user_id == "subj-1"


async def test_no_subject_means_no_clinical_writes(
    context, provider, db_sessionmaker
) -> None:
    context.sessionmaker = db_sessionmaker
    provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'

    await get_graph().ainvoke(
        {"session_id": "s", "user_input": "你好", "subject_id": None}, context=context
    )
    await _drain_background_tasks()
    # The gate still screens — it protects the person, not the record — but
    # nothing may be written without a subject to attribute it to.
    assert not any("信息抽取器" in s for s in provider.route_systems)
    async with db_sessionmaker() as db:
        from sqlalchemy import select

        from app.models_business import ModuleOneRecord, RiskMonitoring

        assert (await db.execute(select(ModuleOneRecord))).scalars().all() == []
        assert (await db.execute(select(RiskMonitoring))).scalars().all() == []


async def test_clinical_writes_are_skipped_without_a_sessionmaker(
    context, provider
) -> None:
    """The graph must still run for a caller with no database wired in."""
    context.sessionmaker = None
    provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'

    result = await get_graph().ainvoke(
        {"session_id": "s", "user_input": "你好", "subject_id": "subj-1"},
        context=context,
    )
    await _drain_background_tasks()
    assert result["final_response"]


async def test_clinical_failure_does_not_affect_the_turn(
    context, provider, db_sessionmaker
) -> None:
    class Broken:
        def __call__(self, *a, **k):
            raise RuntimeError("database is on fire")

    context.sessionmaker = Broken()
    provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'
    provider.extraction_result = '{"chief_complaint": "x"}'

    result = await get_graph().ainvoke(
        {"session_id": "s", "user_input": "我想进入下一步", "subject_id": "subj-1"},
        context=context,
    )
    await _drain_background_tasks()
    assert result["final_response"]
    assert not result.get("error")


async def test_clinical_context_reaches_the_system_prompt(
    context, provider, db_sessionmaker
) -> None:
    """The PA card must be stated as established fact, not re-elicited."""
    from app.clinical_store import persist_module_record

    context.sessionmaker = db_sessionmaker
    await persist_module_record(
        db_sessionmaker,
        module="module_2",
        user_id="subj-1",
        data={"target_activity_content": "楼下散步", "has_target_card_generated": True},
        reuse_latest=False,
    )

    await get_graph().ainvoke(
        {"session_id": "s", "user_input": "你好", "subject_id": "subj-1"},
        context=context,
    )

    system_text = "\n".join(seg.text for seg in provider.systems[0])
    assert "楼下散步" in system_text
    assert "已记录的既有信息" in system_text


# ---------------------------------------------------------------------------
# The risk gate
# ---------------------------------------------------------------------------


async def test_a_flagged_turn_gets_the_crisis_reply_not_the_module(
    context, provider
) -> None:
    provider.risk_result = '{"risk_status": 1, "risk_expression_type": 2}'
    final = await run({"user_input": "我不想活了", "metadata": {}}, context)

    from app.prompts import CRISIS_PROMPT, CRISIS_RESOURCES

    # The module prompt never ran; the crisis prompt did.
    systems = ["\n".join(seg.text for seg in s) for s in provider.systems]
    assert any(CRISIS_PROMPT in s for s in systems)
    assert not any(MODULE_PROMPTS["module_1"] in s for s in systems)
    # Resources are appended by us, not left to the model.
    assert final["final_response"].endswith(CRISIS_RESOURCES)


async def test_a_clean_turn_is_untouched_by_the_gate(context, provider) -> None:
    provider.risk_result = '{"risk_status": 0}'
    final = await run({"user_input": "我今天散步了", "metadata": {}}, context)

    systems = ["\n".join(seg.text for seg in s) for s in provider.systems]
    assert any(MODULE_PROMPTS["module_1"] in s for s in systems)
    assert final["final_response"] == "saw 1 messages"


async def test_a_crisis_turn_never_advances_the_module(context, provider) -> None:
    """A distressed turn must not push someone into goal-setting."""
    provider.risk_result = '{"risk_status": 1}'
    provider.route_result = '{"target_module": "2"}'  # the router would move on
    final = await run(
        {"user_input": "我不想活了", "forced_module": "module_1", "metadata": {}},
        context,
    )
    assert final["next_module"] == "module_1"


async def test_the_gate_fails_open(context, provider) -> None:
    """A broken screen must not block the turn — see risk_gate_node."""
    provider.risk_result = "the model returned prose instead of json"
    final = await run({"user_input": "你好", "metadata": {}}, context)
    assert final["final_response"] == "saw 1 messages"
    assert not final.get("risk")


async def test_the_gate_can_be_turned_off(context, provider) -> None:
    # `Settings` is a pydantic model, not a dataclass — `model_copy`, not
    # `dataclasses.replace`. `GraphContext` around it is a dataclass.
    context = dataclasses.replace(
        context, settings=context.settings.model_copy(update={"risk_gate_enabled": False})
    )
    provider.risk_result = '{"risk_status": 1}'
    final = await run({"user_input": "我不想活了", "metadata": {}}, context)

    # No screening call at all, and the module answered.
    assert not any("风险信号检测器" in s for s in provider.route_systems)
    assert final["final_response"] == "saw 1 messages"


async def test_the_flagged_turn_is_recorded_once(
    context, provider, db_sessionmaker
) -> None:
    """The gate already judged it; the summariser must not screen again."""
    from sqlalchemy import select

    from app.models_business import RiskMonitoring

    context.sessionmaker = db_sessionmaker
    provider.risk_result = '{"risk_status": 1, "risk_expression_type": 3}'

    await run(
        {"user_input": "我不想活了", "subject_id": "subj-1", "metadata": {}}, context
    )
    await _drain_background_tasks()

    async with db_sessionmaker() as db:
        rows = (await db.execute(select(RiskMonitoring))).scalars().all()
    assert len(rows) == 1
    assert rows[0].risk_expression_type == 3
    # Exactly one screening call — not one for the gate and one for the record.
    assert sum("风险信号检测器" in s for s in provider.route_systems) == 1


async def test_crisis_reply_still_carries_resources_if_the_model_fails(
    context, provider
) -> None:
    """The actionable half of a crisis reply must not be lost to an outage."""
    from app.prompts import CRISIS_RESOURCES

    provider.risk_result = '{"risk_status": 1}'
    provider.fail_with = ProviderError("upstream exploded")

    final = await run({"user_input": "我不想活了", "metadata": {}}, context)
    assert CRISIS_RESOURCES in final["final_response"]


# ---------------------------------------------------------------------------
# The registration profile reaching the model
# ---------------------------------------------------------------------------


async def test_registration_profile_reaches_the_system_prompt(
    context, provider, db_sessionmaker
) -> None:
    """The whole registration form was write-only until this.

    Answering "我不能剧烈运动" persisted to `user_profile` and then changed
    nothing about what the coach went on to suggest.
    """
    from app.models_business import UserProfile

    context.sessionmaker = db_sessionmaker
    async with db_sessionmaker() as db:
        db.add(
            UserProfile(
                uuid="subj-1",
                nickname="南瓜",
                communication_preference="温柔引导",
                physical_condition="膝关节损伤",
                behavior_taboo="不能剧烈运动",
            )
        )
        await db.commit()

    await run({"user_input": "你好", "subject_id": "subj-1", "metadata": {}}, context)

    system = "\n".join(seg.text for seg in provider.systems[0])
    assert "南瓜" in system
    assert "温柔引导" in system
    assert "膝关节损伤" in system
    assert "不能剧烈运动" in system
    assert "用户档案" in system


async def test_the_profile_is_present_on_every_turn_not_just_the_first(
    context, provider, db_sessionmaker
) -> None:
    """A physical limit that only applies on turn one is worse than useless.

    Long-term memory and clinical context are deliberately gated to two turns;
    this must not be, so it is loaded ahead of that gate.
    """
    from app.models_business import UserProfile

    context.sessionmaker = db_sessionmaker
    async with db_sessionmaker() as db:
        db.add(UserProfile(uuid="subj-1", behavior_taboo="不能剧烈运动"))
        await db.commit()

    first = await run(
        {"user_input": "一", "subject_id": "subj-1", "metadata": {}}, context
    )
    await run(
        {
            "user_input": "二",
            "session_id": first["session_id"],
            "subject_id": "subj-1",
            "metadata": {},
        },
        context,
    )

    later = "\n".join(seg.text for seg in provider.systems[-1])
    assert "不能剧烈运动" in later, "the profile vanished after the first turn"


async def test_no_profile_block_without_a_subject(context, provider, db_sessionmaker) -> None:
    context.sessionmaker = db_sessionmaker
    await run({"user_input": "你好", "metadata": {}}, context)
    system = "\n".join(seg.text for seg in provider.systems[0])
    assert "用户档案" not in system
