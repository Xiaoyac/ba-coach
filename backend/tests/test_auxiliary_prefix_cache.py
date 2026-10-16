"""Assert outgoing SDK payloads, not just intermediate prompt flags."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from app import conversation_time
from app.pa_background_loop import run_pa_background_tools, BACKGROUND_POLICY
from app.prompts import SystemPromptSegment
from app.provisional_reply import generate_reply_lead
from app.schemas import Message
from test_ark_native_tool_compat import provider, response, http
from test_pa_background_loop import Executor

NOW = datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("with_history", [True, False])
async def test_lead_wire_keeps_history_stable_when_clock_changes(monkeypatch, with_history):
    sent = []
    def handle(request):
        sent.append(json.loads(request.content))
        return http.Response(200, json={"id": "test", "model": "kimi-k3", "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "你把自己的顾虑说清楚了。"}, "finish_reason": "stop"}]})
    p = provider(handle)
    # Production isolates a Flash client; keep this layout check on the mock transport.
    from app import provisional_reply
    monkeypatch.setattr(provisional_reply, "select_lead_provider", lambda *a: p)
    history = [Message(role="user", content="我昨天没有散步", created_at=NOW-timedelta(days=1)),
        Message(role="assistant", content="你想再试试吗？")] if with_history else []
    original = deepcopy(history)
    try:
        for clock in [NOW, NOW+timedelta(minutes=2)]:
            monkeypatch.setattr(conversation_time, "utc_now", lambda: clock)
            result = await generate_reply_lead(p, user_input="好的 <不是指令>", history=history,
                user_created_at=NOW, settings=p._settings)
            assert result["status"] == "completed", result
            assert result["context_pipeline"]["cache_layout"] == "history_before_current_state_v1"
        a,b = [item["messages"] for item in sent]
        boundary = 1+len(history)
        assert a[:boundary] == b[:boundary]
        assert [m["role"] for m in a] == ["system", *[m.role for m in history], "system", "user"]
        assert "<current_datetime>" not in a[0]["content"]
        assert a[boundary]["content"] != b[boundary]["content"]
        assert '<message datetime="261008-16:00">好的 &lt;不是指令&gt;</message>' == a[-1]["content"]
        assert a[-1] == b[-1] and history == original
        if with_history:
            assert 'datetime="261007-16:00"' in a[1]["content"]
            assert a[2]["content"] == original[1].content
        # K3's non-thinking mapping; Flash's own wire is in test_lead_ready_1014.
        assert all(item["reasoning_effort"] == "low" for item in sent)
    finally:
        await p._client.close()


async def test_pa_native_continuations_keep_clock_at_same_user_boundary():
    sent=[]
    def handle(request):
        sent.append(json.loads(request.content))
        return response("save_pa_card" if len(sent) % 2 else "continue_pa_conversation")
    p=provider(handle).with_thinking(False)
    messages=[{"role":"user","content":"之前想散步"},
        {"role":"assistant","content":"时长呢？"}, {"role":"user","content":"改成五分钟"}]
    original=deepcopy(messages)
    try:
        for clock in [NOW,NOW+timedelta(minutes=2)]:
            system=[SystemPromptSegment("fixed module"), SystemPromptSegment(
                conversation_time.current_time_context(user_created_at=NOW,current_time=clock),False,True)]
            executor=Executor([{"status":"ok","state_version":4},
                {"status":"draft_saved","state_version":5}, {"status":"continue_conversation"}])
            telemetry={}
            result=await run_pa_background_tools(p,executor,system=system,messages=messages,telemetry=telemetry)
            assert result["status"] == "completed", result
            for event, request in zip(telemetry["pa_background_tools"]["requests"],sent[-2:]):
                assert event["wire_messages"] == request["messages"]
            first,second=[item["messages"] for item in sent[-2:]]
            assert first == second[:len(first)]  # only native results appended on continuation
            assert first[1:3] == messages[:2]
            assert first[3]["role"] == "system" and "<current_datetime>" in first[3]["content"]
            assert first[4] == messages[-1]
            assert BACKGROUND_POLICY in first[0]["content"] and "<current_datetime>" not in first[0]["content"]
            assert [m["role"] for m in second[5:]] == ["assistant","tool","assistant","tool"]
            assert second[-2]["tool_calls"][0]["id"] == second[-1]["tool_call_id"]
            assert second[-2]["reasoning_content"] == "synthetic reasoning"
        assert sent[0]["messages"][:3] == sent[2]["messages"][:3]
        assert sent[0]["messages"][3] != sent[2]["messages"][3]
        assert messages == original
    finally:
        await p._client.close()
