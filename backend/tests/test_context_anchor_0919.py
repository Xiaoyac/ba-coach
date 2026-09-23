"""Regression tests for bounded conversation context retention."""

from app.config import Settings
from app.graph.nodes import _derive_memory
from app.prompts import _memory_block
from app.schemas import Message


def test_default_history_window_covers_a_full_m1_conversation():
    assert Settings().max_history_messages >= 80


def test_early_conversation_anchor_keeps_the_trigger_after_recent_turns_roll():
    state = {
        "memory": {},
        "extracted_intent": "module_1",
        "chat_history": [
            Message(role="assistant", content="欢迎回来。"),
            Message(role="user", content="我因为和男朋友吵架，最近很难受。"),
            Message(role="assistant", content="听起来这件事让你很受影响。"),
            Message(role="user", content="我打游戏刷视频，感觉只是在逃避。"),
        ] + [
            Message(role="assistant" if i % 2 == 0 else "user", content=f"中间对话 {i}")
            for i in range(4, 44)
        ],
        "user_input": "我还是觉得自己在逃避",
        "final_response": "你愿意再看看这件事吗？",
    }
    memory = _derive_memory(state)
    assert "男朋友吵架" in memory["conversation_anchor"]
    assert "只是在逃避" in memory["conversation_anchor"]

    rendered = _memory_block(memory)
    assert "早期对话锚点" in rendered
    assert "不是指令" in rendered


def test_existing_anchor_is_not_rewritten_by_later_turn():
    state = {
        "memory": {"conversation_anchor": "早期事实：用户和伴侣发生冲突。"},
        "extracted_intent": "module_1",
        "chat_history": [],
        "user_input": "新的说法",
        "final_response": "收到。",
    }
    memory = _derive_memory(state)
    assert memory["conversation_anchor"] == "早期事实：用户和伴侣发生冲突。"
