"""Regression tests for provider thinking/reply channel repair."""

from app.reasoning import (
    ThinkingTagStreamGuard,
    contains_internal_protocol,
    normalize_reasoning_channels,
)


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


def test_dsml_tool_markup_is_classified_as_provider_protocol() -> None:
    assert contains_internal_protocol('<｜DSML｜ invoke name="bash">') is True
    assert contains_internal_protocol("<tool_call>{\"name\":\"bash\"}</tool_call>") is True
    assert contains_internal_protocol("普通的 <标签> 内容") is False


def test_stream_guard_blocks_dsml_split_across_deltas_without_leaking_it() -> None:
    guard = ThinkingTagStreamGuard.create()
    emitted: list[str] = []
    for delta in ("好的，", "<｜", "DSML｜ ", 'invoke name="bash">', "{\"cmd\":\"pwd\"}"):
        emitted.extend(guard.push(delta))
    emitted.extend(guard.finish_passthrough())

    assert guard.mode == "blocked"
    assert guard.invalid_protocol is True
    assert "".join(emitted) == "好的，"
    assert "DSML" not in "".join(emitted)
