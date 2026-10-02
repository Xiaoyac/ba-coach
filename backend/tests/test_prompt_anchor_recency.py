"""Keep recent turns in their message roles rather than duplicating them as system data."""
import dataclasses
from datetime import datetime, timezone, timedelta

import pytest

from app.graph import get_graph
from app.prompts import _memory_block, build_system_segments
from app.providers.base import as_text
from app.schemas import Message


SENT = datetime(2026, 9, 30, 6, 8, 16, tzinfo=timezone.utc)
OLD_REPLY = "你好。你记得咱们定的目标是什么吗？"
ANCHOR = "[2026-09-30T14:08:16+08:00（星期三）] 用户：nihao\n[时间未知] 教练：" + OLD_REPLY
HISTORY = [Message(role="user", content="nihao", created_at=SENT),
           Message(role="assistant", content=OLD_REPLY),
           Message(role="user", content="nihao", created_at=SENT + timedelta(seconds=20)),
           Message(role="assistant", content="你还记得想做什么活动吗？")]


def test_duplicate_anchor_is_suppressed_without_mutating_memory_or_other_facts():
    memory = {"conversation_anchor": ANCHOR, "pa_card": "历史方案保留", "other_fact": "历史附注保留"}
    saved = dict(memory)
    text = _memory_block(memory, HISTORY)
    assert "nihao" not in text and OLD_REPLY not in text
    assert "历史方案保留" in text and "历史附注保留" in text
    assert memory == saved


@pytest.mark.parametrize("history", [[], HISTORY[2:], list(reversed(HISTORY[:2])),
    [Message(role="assistant", content="nihao", created_at=SENT), HISTORY[1]],
    [Message(role="user", content="nihao", created_at=SENT + timedelta(days=1)), HISTORY[1]],
    [Message(role="user", content="nihao"), HISTORY[1]], [HISTORY[0]]])
def test_unseen_or_differently_dated_anchor_is_preserved(history):
    assert ANCHOR in _memory_block({"conversation_anchor": ANCHOR}, history)


def test_unknown_legacy_summary_is_not_discarded():
    anchor = "早期事实：用户提及一项重要经历。"
    assert anchor in _memory_block({"conversation_anchor": anchor}, HISTORY)


def test_source_excerpt_matches_whitespace_and_truncated_message():
    anchor = "用户：开头 很长\n教练：已收到"
    history = [Message(role="user", content="开头\n很长 后续完整正文"),
               Message(role="assistant", content="已收到。")]
    assert _memory_block({"conversation_anchor": anchor}, history) == ""


def test_admin_policy_and_current_user_are_not_modified():
    segments = build_system_segments("module_3", memory={"conversation_anchor": ANCHOR},
        history=HISTORY, global_prompt="管理员规则 {literal}", module_prompt="管理员模块规则")
    assert segments[0].text == "管理员规则 {literal}"
    assert segments[1].text.endswith("管理员模块规则")
    assert "nihao" not in as_text(segments)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("window", [80, 2])
async def test_real_graph_uses_only_visible_history_for_deduplication(context, provider, stream, window):
    context = dataclasses.replace(context, stream=stream,
        settings=context.settings.model_copy(update={"max_history_messages": window}))
    session = await context.store.get_or_create(None)
    for message in HISTORY:
        await context.store.append(session.session_id, message)
    await context.store.set_memory(session.session_id, {"conversation_anchor": ANCHOR})
    state = {"session_id": session.session_id, "user_input": "我们说要一起去商场玩鬼抓人",
             "forced_module": "module_3"}
    if stream:
        async for _ in get_graph().astream(state, context=context):
            pass
    else:
        await get_graph().ainvoke(state, context=context)
    assert len(provider.seen) == 1
    assert "我们说要一起去商场玩鬼抓人" in provider.seen[0][-1].content
    assert (ANCHOR in as_text(provider.systems[0])) is (window == 2)
