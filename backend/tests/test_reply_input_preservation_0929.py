"""Input cleanup must not discard dialogue or mutate durable memory.

Exercise the actual reply-generation node with a recording provider, including
the streaming path used online. No real model or external service is called.
"""

from copy import deepcopy
from dataclasses import replace

import pytest
from langgraph.runtime import Runtime

from app.graph.nodes import ModuleConfig, make_module_node
from app.prompts import _memory_block
from app.providers.base import as_text
from app.schemas import Message


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("module", [f"module_{number}" for number in range(1, 5)])
async def test_reply_projection_preserves_history_facts_and_admin_policy(
    context, provider, module, stream
):
    history = [
        Message(
            role="user" if number % 2 == 0 else "assistant",
            content=f"原样历史第{number:02d}条\n保留标点、空格  和内容。",
        )
        for number in range(80)
    ]
    history[-3:] = [
        Message(role="assistant", content="之前讨论的是五分钟。"),
        Message(role="user", content="时间到10分钟吧，动作停留久一点"),
        Message(role="assistant", content="收到你提出的10分钟修改。"),
    ]
    current = "Ok老大，你是彻底接替模块2了嘛"
    memory = {
        "conversation_anchor": "用户：早期说过和朋友发生争执。\n教练：我们一起看看。",
        "pa_card": "历史展示卡原文：午间坐着舒展五分钟。",
        "m2_activity_context": {
            "intention": {
                "value": "action", "message_id": 301,
                "quote": "我愿意先试一次晒太阳。",
            },
            "trial": {
                "state": "trial_active", "source_role": "trial",
                "activity_content": "晒太阳", "message_id": 301,
                "quote": "我愿意先试一次晒太阳。",
            },
            "secondary_activities": [{
                "activity_content": "散步", "message_id": 303,
                "quote": "另外还在楼下散步七分钟。", "duration": "七分钟",
            }],
        },
        "last_user_message": "只供后台使用的上一轮输入",
        "last_module": "module_1",
        "turn_count": "51",
        "module_extraction_freshness": {
            "module_2": {"assistant_message_id": 888, "cycle_id": "old-cycle"},
        },
        "routing_mode": "router_only",
        "dialogue_draft": {"record_hash": "internal-hash", "version": 7},
    }
    clinical = [
        "已记录用户事实：用户说运动后心情有一点改善。",
        "曾解释的BA内容：行动可能带来新的反馈，不保证立刻开心。",
        "用户对解释的回应原文：我理解了，但今天暂时不想定目标。",
    ]
    profile = ["膝关节损伤；不能剧烈运动；称呼阿花。"]
    long_term = ["用户长期偏好安静且人少的活动环境。"]
    global_policy = "管理员全局策略唯一标记：尊重用户表达。"
    module_policy = f"管理员{module}策略唯一标记：自然承接当前问题。"
    snapshot = {"global": global_policy, module: module_policy}
    state = {
        "session_id": "preservation-check",
        "user_message_id": 901,
        "user_input": current,
        "chat_history": history,
        "memory": memory,
        "clinical_context": clinical,
        "profile_context": profile,
        "long_term_memory": long_term,
        "current_module": module,
        "extracted_intent": module,
        "routing_mode": "router_only",
    }
    before = deepcopy(state)
    context = replace(context, stream=stream, prompt_snapshot=snapshot)
    events = []
    result = await make_module_node(module, ModuleConfig(retrieve=False))(
        state, Runtime(context=context), writer=events.append,
    )

    # All 80 prior messages retain their text/order. User messages carry the
    # timestamp envelope; this fixture deliberately has no known timestamps.
    from html import escape
    expected = [Message(role=m.role, content=(
        f'<message datetime="unknown">{escape(m.content, quote=False)}</message>'
        if m.role == 'user' else m.content
    )) for m in history] + [Message(
        role="user", content=f'<message datetime="unknown">{escape(current, quote=False)}</message>',
    )]
    assert len(provider.seen) == 1
    # Compare the wire contract, not Pydantic's private source-content metadata.
    assert [(m.role, m.content) for m in provider.seen[0]] == [
        (m.role, m.content) for m in expected
    ]
    assert len(provider.seen[0]) == 81
    assert result["telemetry"]["main_input"]["messages"] == [
        {"role": message.role, "content": message.content} for message in expected
    ]

    system = as_text(provider.systems[0])
    assert system.count(global_policy) == 1
    assert system.count(module_policy) == 1
    assert result["telemetry"]["main_input"]["system"] == system
    for fact in clinical + profile + long_term:
        assert fact in system
    for fact in (
        "早期说过和朋友发生争执。", "历史展示卡原文：午间坐着舒展五分钟。",
        "我愿意先试一次晒太阳。", "另外还在楼下散步七分钟。",
    ):
        assert fact in system
    assert "只供后台使用的上一轮输入" not in system
    for diagnostic in (
        "module_extraction_freshness", "old-cycle", "dialogue_draft",
        "internal-hash", "trial_active", "routing_mode",
    ):
        assert diagnostic not in system

    # Projection is read-only; extraction, routing and persistence retain their
    # original memory structures, including operational keys hidden in replies.
    assert state == before
    assert snapshot == {"global": global_policy, module: module_policy}
    assert provider.route_calls == []
    if stream:
        assert "".join(
            event.get("text", "") for event in events if event.get("type") == "delta"
        ) == result["final_response"]


def test_runtime_projection_keeps_legacy_facts_without_exposing_control_keys():
    memory = {
        "early_preference": "以前说过不喜欢在很吵的地方活动。",
        "conversation_anchor": "用户：小时候常和姐姐一起散步。",
        "pa_card": "教练曾展示的目标卡：早晨五分钟。",
        "fresh_m1": True,
        "current_module": "internal-current-module",
        "next_module": "internal-next-module",
        "current_phase": "internal-phase",
        "phase": "internal-old-phase",
        "current_step": "internal-step",
        "module_steps": {"module_1": ["internal-finished-step"]},
        "sandbox_mode": "internal-sandbox-mode",
        "sandbox_start_module": "internal-sandbox-start",
        "current_transition_evidence": {"quote": "internal-transition-proof"},
    }
    before = deepcopy(memory)
    text = _memory_block(memory)
    for retained in (
        memory["early_preference"], memory["conversation_anchor"], memory["pa_card"],
    ):
        assert retained in text
    for hidden in (
        "fresh_m1", "internal-current-module", "internal-next-module",
        "internal-phase", "internal-old-phase", "internal-step",
        "internal-finished-step", "internal-sandbox-mode",
        "internal-sandbox-start", "internal-transition-proof",
    ):
        assert hidden not in text
    assert memory == before
