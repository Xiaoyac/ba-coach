"""Regression tests for provider thinking/reply channel repair."""

from app.reasoning import ThinkingTagStreamGuard, normalize_reasoning_channels


def test_embedded_thinking_block_is_moved_out_of_visible_reply() -> None:
    normalized = normalize_reasoning_channels(
        "<thinking>内部分析</thinking>\n\n给用户的回答",
        "response另一个回答候选",
    )
    assert normalized.reply == "给用户的回答"
    assert normalized.reasoning == "内部分析"
    assert normalized.repaired is True


def test_response_marker_in_reasoning_is_not_disclosed_as_thought() -> None:
    normalized = normalize_reasoning_channels(
        "最终回答",
        "response这是重复的回答候选",
    )
    assert normalized.reply == "最终回答"
    assert normalized.reasoning == ""
    assert normalized.repaired is True


def test_response_candidate_recovers_a_missing_visible_answer() -> None:
    normalized = normalize_reasoning_channels("", "response恢复出来的回答")
    assert normalized.reply == "恢复出来的回答"
    assert normalized.reasoning == ""


def test_unclosed_thinking_never_becomes_visible_reply() -> None:
    normalized = normalize_reasoning_channels(
        "<thinking>尚未结束的内部分析",
        "response可用回答",
    )
    assert normalized.reply == "可用回答"
    assert normalized.reasoning == "尚未结束的内部分析"


def test_stream_guard_releases_normal_prose_after_short_prefix() -> None:
    guard = ThinkingTagStreamGuard.create()
    assert guard.push("你") == ["你"]
    assert guard.push("好") == ["好"]
    assert guard.raw == "你好"


def test_stream_guard_holds_split_thinking_tag() -> None:
    guard = ThinkingTagStreamGuard.create()
    assert guard.push("<thi") == []
    assert guard.push("nking>内部") == []
    assert guard.push("</thinking>回答") == []
    assert guard.mode == "blocked"
    assert guard.raw == "<thinking>内部</thinking>回答"
