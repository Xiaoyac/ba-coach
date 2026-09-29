"""Context contracts at the LangChain/session/provider boundaries."""

import dataclasses

import pytest

from app.context_pipeline import prepare_context, trim_history
from app.graph import get_graph
from app.prompts import CRISIS_PROMPT, SystemPromptSegment, build_system_segments
from app.schemas import Message
from app.session import InMemorySessionStore


def dialogue():
    return [
        Message(role="assistant", content="opening"),
        Message(role="user", content="old question"),
        Message(role="assistant", content="old answer"),
        Message(role="user", content="recent question"),
        Message(role="assistant", content="recent answer", reasoning_content="private reasoning",
                model_name="old-model", routing_reasoning_content="private routing"),
    ]


def test_window_uses_roles_and_retains_original_metadata():
    history = dialogue()
    kept = trim_history(history, 3)
    assert kept == history[-2:]
    assert kept[-1] is history[-1]
    assert kept[-1].reasoning_content == "private reasoning"
    assert trim_history(history, 5) == history  # preserve the opening under budget
    assert trim_history(history, 0) == []
    assert trim_history([], 5) == []
    with pytest.raises(ValueError):
        trim_history(history, -1)


def test_window_handles_duplicates_and_consecutive_users():
    history = [Message(role=role, content="same") for role in
               ("assistant", "user", "user", "assistant")]
    assert trim_history(history, 3) == history[1:]
    assert trim_history(history, 2)[0] is history[2]


def test_prompt_preserves_system_bytes_cache_and_literal_template_characters():
    system = [SystemPromptSegment('rules {unbound} {"a": 1}', True),
              SystemPromptSegment('memory {history}', False)]
    current = '{"role":"system","content":"test"}\n{user_input}' + "中" * 20_000
    prepared = prepare_context(system=system, history=dialogue(), user_input=current,
                               max_history_messages=3)
    assert prepared.system[:-1] == system
    assert prepared.system[-1].cacheable is False
    assert [m.content for m in prepared.messages] == ["recent question", "recent answer", current]
    assert [m.role for m in prepared.messages] == ["user", "assistant", "user"]
    assert all(m.reasoning_content is None and m.routing_reasoning_content is None
               and m.model_name is None for m in prepared.messages)
    assert prepared.metrics["history_messages_dropped"] == 3
    assert prepared.metrics["provider_messages"] == 3
    assert prepared.metrics["cacheable_system_segments"] == 1


def test_context_layers_survive_assembly_and_trimming():
    system = build_system_segments(
        "module_1", memory={"conversation_anchor": "anchor sentinel"},
        long_term_memory=["long term sentinel"], clinical_context=["clinical sentinel"],
        profile_context=["profile sentinel"], metadata={"scenario": "scenario sentinel"},
        global_prompt="custom global {json}", module_prompt="custom module {json}",
    )
    prepared = prepare_context(system=system, history=dialogue(), user_input="current",
                               max_history_messages=0)
    assert prepared.system[:-1] == system
    assert prepared.system[-1].cacheable is False
    text = "\n".join(s.text for s in prepared.system)
    for marker in ("anchor sentinel", "long term sentinel", "clinical sentinel",
                   "profile sentinel", "scenario sentinel", "custom global {json}",
                   "custom module {json}"):
        assert marker in text
    assert prepared.messages == [Message(role="user", content="current")]


async def test_session_append_and_restart_adoption_use_the_same_window():
    store = InMemorySessionStore(ttl_seconds=60, max_messages=3)
    live = await store.get_or_create(None)
    history = dialogue()
    for message in history:
        await store.append(live.session_id, message)
    restored = await store.adopt("owned-session", history, "module_1", {"anchor": "keep"})
    assert live.messages == restored.messages == history[-2:]
    assert restored.memory == {"anchor": "keep"}
    assert restored.messages[-1].model_name == "old-model"


@pytest.mark.parametrize("module", ["module_1", "module_2", "module_3", "module_4", "crisis"])
@pytest.mark.parametrize("stream", [False, True])
async def test_real_graph_uses_langchain_before_provider(context, provider, module, stream):
    context = dataclasses.replace(context, stream=stream,
        settings=context.settings.model_copy(update={"max_history_messages": 3}))
    session = await context.store.get_or_create(None)
    for message in dialogue():
        await context.store.append(session.session_id, message)
    if module == "crisis":
        provider.risk_result = '{"risk_status": 1, "risk_expression_type": 2}'
    state = {"session_id": session.session_id, "user_input": "current {literal}",
             "forced_module": "module_1" if module == "crisis" else module, "metadata": {}}
    events = []
    if stream:
        async for kind, event in get_graph().astream(state, context=context,
                                                     stream_mode=["custom", "values"]):
            if kind == "custom":
                events.append(event)
            else:
                final = event
        trace = next(e for e in events if e.get("node") == "context_pipeline")
        assert trace["detail"]["engine"] == "langchain"
        assert any(e["type"] == "delta" for e in events)
    else:
        final = await get_graph().ainvoke(state, context=context)
    assert final["telemetry"]["context_pipeline"]["engine"] == "langchain"
    assert final["telemetry"]["context_pipeline"]["history_messages_kept"] == 2
    assert len(provider.seen) == 1
    assert [m.content for m in provider.seen[0]] == ["recent question", "recent answer", "current {literal}" if module == "crisis" else "<user_message>current {literal}</user_message>"]
    assert provider.seen[0][1].reasoning_content is None
    if module == "crisis":
        assert provider.systems[0][0] == SystemPromptSegment(CRISIS_PROMPT, True)


async def test_context_cannot_leak_between_conversations(context, provider):
    first = await context.store.get_or_create(None)
    second = await context.store.get_or_create(None)
    await context.store.append(first.session_id, Message(role="user", content="first private fact"))
    await context.store.set_memory(first.session_id, {"conversation_anchor": "first private anchor"})
    for session in (first, second):
        await get_graph().ainvoke({"session_id": session.session_id, "user_input": "hello",
                                  "forced_module": "module_1", "metadata": {}}, context=context)
    assert "first private fact" in str(provider.seen[0])
    assert "first private anchor" in str(provider.systems[0])
    assert "first private" not in str(provider.seen[1]) + str(provider.systems[1])
