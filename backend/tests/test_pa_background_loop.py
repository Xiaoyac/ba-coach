"""Private PA tool jobs never generate or stream a foreground reply."""
import asyncio
from copy import deepcopy
import json

import pytest

from app.pa_background_loop import BACKGROUND_POLICY, run_pa_background_tools
from app.pa_card_tools import definitions
from app.providers.base import ProviderError, StreamDelta, as_text
from app.schemas import Message


def call(name, args=None, *, id=None):
    return {"id": id or name, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args or {}, ensure_ascii=False),
    }}


class Provider:
    model = "background-model"

    def __init__(self, batches):
        self.batches, self.requests = batches, []

    async def stream_tools(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        batch = self.batches[len(self.requests) - 1]
        yield StreamDelta(kind="reasoning", text="private reasoning")
        yield StreamDelta(kind="content", text="private unsolicited wording")
        yield StreamDelta(kind="tool_calls", tool_calls=batch)
        yield StreamDelta(kind="usage", usage={"input_tokens": 5, "output_tokens": 2},
                          request_id=f"request-{len(self.requests)}", finish_reason="tool_calls")


class Executor:
    module = "module_2"
    ui_enabled = False

    def __init__(self, results):
        self.definitions = definitions(self.module)
        self.results, self.calls, self.trace = results, [], []
        self.active = False

    async def execute(self, native_call):
        assert not self.active, "Database writes must not overlap"
        self.active = True
        try:
            await asyncio.sleep(0)
            self.calls.append(deepcopy(native_call))
            return deepcopy(self.results[len(self.calls) - 1])
        finally:
            self.active = False


async def run(provider, executor, **kwargs):
    return await run_pa_background_tools(provider, executor, system="ordinary module policy",
        messages=[Message(role="user", content="请保存计划")], **kwargs)


async def test_native_history_is_private_and_terminal_has_no_reply_request():
    provider = Provider([[call("save_pa_card", {"state_version": 4})],
                         [call("continue_pa_conversation")]])
    executor = Executor([{"status": "ok", "state_version": 4},
                         {"status": "draft_saved", "state_version": 5},
                         {"status": "continue_conversation"}])
    telemetry = {"main_input": {"preserved": True}, "main_generation": {"usage": 17}}
    result = await run(provider, executor, telemetry=telemetry)
    assert result["status"] == "completed"
    assert result["outcome"] == "continue_conversation"
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 4}
    assert result["request_id"] == "request-2"
    assert result["rounds"] == len(provider.requests) == 2
    assert provider.requests[0]["tool_choice"] == "required"
    assert [r["tool_choice"] for r in provider.requests] == ["required", "required"]
    assert as_text(provider.requests[-1]["system"]).endswith(BACKGROUND_POLICY)
    history = provider.requests[0]["messages"]
    assert history[-2]["role"] == "assistant"
    assert history[-2]["tool_calls"] == [call("get_pa_card", id="server_initial_pa_read")]
    assert "reasoning_content" not in history[-2]
    assert history[-1]["role"] == "tool"
    assert history[-1]["tool_call_id"] == "server_initial_pa_read"
    assert json.loads(history[-1]["content"])["state_version"] == 4
    assert provider.requests[0]["messages"][0] == {"role": "user", "content": "请保存计划"}
    assert telemetry["main_input"] == {"preserved": True}
    assert telemetry["main_generation"] == {"usage": 17}
    trace = telemetry["pa_background_tools"]
    assert trace["status"] == "completed"
    assert len(trace["requests"]) == 2 and len(trace["calls"]) == 3
    assert trace["requests"][0]["duration_ms"] >= 0
    assert "private unsolicited wording" not in str(result)


@pytest.mark.parametrize("status", ["continue_conversation", "ready_to_display", "confirmed",
    "secondary_confirmed", "goal_card_paused", "closed", "saved_activity_context"])
async def test_every_explicit_terminal_stops_without_final_wording(status):
    provider = Provider([[call("continue_pa_conversation"),
                         call("save_pa_card", {"state_version": 4})]])
    executor = Executor([{"status": "ok"}, {"status": status}])
    telemetry = {}
    result = await run(provider, executor, telemetry=telemetry)
    assert result["outcome"] == status
    assert result["status"] == "completed"
    assert len(provider.requests) == 1 and len(executor.calls) == 2
    assert telemetry["pa_background_tools"]["calls"][-1]["executed"] is False
    # Even an unexecuted tail has an honest tool response matching its call ID.
    assert telemetry["pa_background_tools"]["history"][-1]["tool_call_id"] == "save_pa_card"


async def test_tools_refresh_after_each_round_and_ui_policy_is_background_only():
    class DynamicExecutor(Executor):
        ui_enabled = True

        def available_tools(self):
            names = {"get_pa_card"} if not self.calls else {"continue_pa_conversation"}
            return [t for t in self.definitions if t["function"]["name"] in names]

    provider = Provider([[call("continue_pa_conversation")]])
    result = await run(provider, DynamicExecutor([{"status": "ok"}, {"status": "continue_conversation"}]))
    assert result["status"] == "completed"
    assert [t["function"]["name"] for t in provider.requests[0]["tools"]] == ["continue_pa_conversation"]
    assert "网页目标卡交互" in as_text(provider.requests[0]["system"])
    assert as_text(provider.requests[0]["system"]).endswith(BACKGROUND_POLICY)


async def test_sequential_mutations_can_requery_after_version_conflict():
    provider = Provider([
        [call("save_pa_card", {"state_version": 4}), call("present_pa_card", {"state_version": 4}, id="present-1")],
        [call("get_pa_card", id="read-2")],
        [call("present_pa_card", {"state_version": 5}, id="present-2")],
    ])
    executor = Executor([
        {"status": "ok", "state_version": 4}, {"status": "draft_saved", "state_version": 5},
        {"status": "blocked", "reason": "state_changed_requery_state"},
        {"status": "ok", "state_version": 5}, {"status": "ready_to_display"},
    ])
    result = await run(provider, executor)
    assert result["status"] == "completed"
    assert len(executor.calls) == 5
    assert "get_pa_card" not in [t["function"]["name"] for t in provider.requests[0]["tools"]]
    assert "get_pa_card" in [t["function"]["name"] for t in provider.requests[1]["tools"]]


async def test_duplicate_write_does_not_execute_again():
    provider = Provider([[call("save_pa_card", {"state_version": 4}, id="save-1")],
                         [call("save_pa_card", {"state_version": 4}, id="save-2")]])
    executor = Executor([{"status": "ok"}, {"status": "draft_saved"}])
    result = await run(provider, executor)
    assert result["reason"] == "repeated_identical_call_limit"
    assert result["status"] == "failed"
    assert len(executor.calls) == 2


async def test_identical_failed_query_is_not_repeated():
    provider = Provider([])
    executor = Executor([{"status": "blocked", "reason": "invalid_arguments"}])
    result = await run(provider, executor)
    assert result["reason"] == "invalid_arguments"
    assert len(executor.calls) == 1
    assert not provider.requests


async def test_explicit_native_messages_preserve_tool_and_reasoning_fields():
    messages = [{"role": "assistant", "content": None, "reasoning_content": "prior reasoning",
                 "tool_calls": [call("get_pa_card", id="prior-read")]},
                {"role": "tool", "tool_call_id": "prior-read", "content": '{"status":"ok"}'},
                {"role": "user", "content": "确认"}]
    provider = Provider([[call("continue_pa_conversation")]])
    await run_pa_background_tools(provider, Executor([{"status": "ok"}, {"status": "continue_conversation"}]),
                                  system="policy", messages=messages)
    assert provider.requests[0]["messages"][:len(messages)] == messages
    assert len(messages) == 3


@pytest.mark.parametrize("reason", ["owned_conversation_unavailable", "current_user_boundary_changed",
    "write_window_closed", "module_changed_requery_state"])
async def test_lost_ownership_or_boundary_stops_immediately(reason):
    provider = Provider([[call("save_pa_card", {"state_version": 4}),
                                              call("present_pa_card", {"state_version": 5})]])
    executor = Executor([{"status": "ok"}, {"status": "blocked", "reason": reason}])
    result = await run(provider, executor)
    assert result["status"] == "failed"
    assert result["reason"] == reason
    assert len(executor.calls) == 2
    assert len(provider.requests) == 1


async def test_changed_module_read_does_not_continue_old_job():
    provider = Provider([])
    result = await run(provider, Executor([{"status": "ok", "module": "module_3"}]))
    assert result["reason"] == "module_changed_requery_state"


async def test_budget_is_exact_and_plain_text_is_not_a_tool_decision():
    provider = Provider([[call("save_pa_card", {"state_version": 1})]])
    result = await run(provider, Executor([{"status": "ok"}, {"status": "draft_saved"}]), max_rounds=1)
    assert result["reason"] == "tool_budget_exhausted"
    assert len(provider.requests) == 1
    result = await run(Provider([[]]), Executor([{"status": "ok"}]))
    assert result["reason"] == "required_tool_decision_missing"


async def test_initial_database_read_precedes_any_model_request():
    class Ordered(Provider):
        async def stream_tools(self, **kwargs):
            assert executor.calls[0]['function']['name'] == 'get_pa_card'
            assert 'get_pa_card' not in [t['function']['name'] for t in kwargs['tools']]
            async for delta in super().stream_tools(**kwargs): yield delta
    executor = Executor([{'status':'ok'}, {'status':'continue_conversation'}])
    provider = Ordered([[call('continue_pa_conversation')]])
    result = await run(provider, executor)
    assert result['status'] == 'completed' and len(provider.requests) == 1


async def test_unknown_tool_and_duplicate_ids_are_rejected_before_execution():
    executor = Executor([{"status": "ok"}])
    result = await run(Provider([[call("execute_sql")]]), executor)
    assert result["reason"] == "tool_not_available"
    assert len(executor.calls) == 1
    executor = Executor([{"status":"ok"}])
    result = await run(Provider([[call("continue_pa_conversation"), call("continue_pa_conversation")]]), executor)
    assert result["reason"] == "duplicate_tool_call_id"
    assert len(executor.calls) == 1


async def test_provider_failure_and_timeout_have_separate_diagnostics():
    class FailingProvider:
        async def stream_tools(self, **kwargs):
            yield StreamDelta(kind="usage", usage={"input_tokens": 3}, request_id="upstream-id")
            raise ProviderError("unavailable")

    telemetry = {}
    result = await run(FailingProvider(), Executor([{"status":"ok"}]), telemetry=telemetry)
    assert result["reason"] == "provider_error"
    assert result["usage"] == {"input_tokens": 3}
    assert result["request_id"] == "upstream-id"
    assert telemetry["pa_background_tools"]["requests"][0]["error_code"] == "provider_error"

    closed = asyncio.Event()

    class WaitingProvider:
        async def stream_tools(self, **kwargs):
            try:
                await asyncio.Event().wait()
                yield StreamDelta(kind="content", text="unreachable")
            finally:
                closed.set()

    result = await run(WaitingProvider(), Executor([{"status":"ok"}]), timeout_seconds=0.01)
    assert result["reason"] == "total_timeout"
    assert closed.is_set()


async def test_cancellation_propagates_and_closes_provider_without_writes():
    entered, closed = asyncio.Event(), asyncio.Event()

    class WaitingProvider:
        async def stream_tools(self, **kwargs):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield StreamDelta(kind="content", text="unreachable")
            finally:
                closed.set()

    telemetry, executor = {}, Executor([{"status":"ok"}])
    task = asyncio.create_task(run(WaitingProvider(), executor, telemetry=telemetry))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert len(executor.calls) == 1
    assert telemetry["pa_background_tools"]["status"] == "cancelled"
    assert telemetry["pa_background_tools"]["error"] == "cancelled"


async def test_timeout_cancels_inflight_mutation_and_does_not_run_batch_tail():
    entered, rolled_back = asyncio.Event(), asyncio.Event()

    class SlowExecutor(Executor):
        async def execute(self, native_call):
            if native_call["function"]["name"] == "get_pa_card":
                return await super().execute(native_call)
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                rolled_back.set()

    executor = SlowExecutor([{"status": "ok"}])
    provider = Provider([[call("save_pa_card"), call("present_pa_card")]])
    telemetry = {}
    result = await run(provider, executor, timeout_seconds=0.01, telemetry=telemetry)
    assert result["reason"] == "total_timeout"
    assert entered.is_set() and rolled_back.is_set()
    assert len(executor.calls) == 1
    calls = telemetry["pa_background_tools"]["calls"]
    assert calls[-2]["result"] == {"status": "unknown", "reason": "execution_cancelled"}
    assert calls[-1]["executed"] is False
