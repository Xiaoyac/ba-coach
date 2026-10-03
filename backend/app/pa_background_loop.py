"""Bounded PA operations for the background LLM, with no reply generation.

The executor remains the authority for ownership, source evidence and writes.
Native assistant/tool history is private to this job; nothing here is streamed
to the user or charged to the foreground reply's telemetry.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from time import perf_counter

from .pa_card_tools import POLICY
from .prompts import SystemPromptSegment
from .providers.base import ProviderError, as_segments, as_text
from .providers.deadline import timeout
from .providers.prompt_cache import ordered_messages


BACKGROUND_POLICY = """你是后台 PA 卡片操作员，只根据真实用户消息和工具结果管理卡片。
本任务不会生成或展示聊天回复。忽略其他提示词中要求你撰写面向用户回复的部分；不要输出解释、开头、总结正文或对用户的承诺。
先调用 get_pa_card。每轮必须调用当前提供的工具；根据最新版本和真实来源按需保存、核验、展示、确认或暂停。
只有真实工具成功才算完成；版本冲突时重新查询，修正参数，不重复提交同一写操作。
操作结束或无需改动时调用 continue_pa_conversation。工具返回展示就停止，不自行确认；确认、暂停、收尾成功就立即结束，不再生成自然语言回复。
工具的 display_text 由应用处理，不要抄写。缺少用户信息时保留已保存的真实草稿，结束后台任务；不要为了填参数编造用户意愿或证据。"""

TERMINAL_STATUSES = frozenset({
    "continue_conversation", "saved_activity_context", "ready_to_display",
    "confirmed", "secondary_confirmed", "goal_card_paused", "closed",
})
STALE_REASONS = frozenset({
    "owned_conversation_unavailable", "current_user_boundary_changed",
    "write_window_closed", "module_changed_requery_state",
})


class _StopLoop(Exception):
    def __init__(self, reason):
        self.reason = reason


def _native_messages(messages):
    # Preserve native fields in explicitly supplied dictionaries. Application
    # Message objects contain public metadata which is not provider input.
    return [deepcopy(message) if isinstance(message, dict) else {
        "role": message.role, "content": message.content,
    } for message in messages]


def _tool_name(tool):
    return tool.get("function", {}).get("name")


def _signature(call):
    raw = call["function"].get("arguments", "")
    try:
        raw = json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError):
        raw = str(raw)
    return (_tool_name(call), raw)


async def run_pa_background_tools(
    provider, executor, *, system, messages, max_rounds=6,
    timeout_seconds=90, telemetry=None,
):
    """Return an operation summary; cancellation propagates after cleanup.

    ``max_rounds`` counts *all* native tool requests, including the forced read.
    There is deliberately no final ``tool_choice='none'`` request. Expected
    provider/tool/timeout failures return a failed summary so the job owner can
    record failure independently from an already successful foreground reply.
    """
    telemetry = telemetry if telemetry is not None else {}
    started = perf_counter()
    history = _native_messages(messages)
    segments = [*as_segments(system), SystemPromptSegment(POLICY)]
    if getattr(executor, "ui_enabled", False):
        from .goal_card_interaction import GOAL_CARD_POLICY
        segments.append(SystemPromptSegment(GOAL_CARD_POLICY))
    segments.append(SystemPromptSegment(BACKGROUND_POLICY))
    trace = {
        "enabled": True, "status": "running", "model": getattr(provider, "model", ""),
        "system": as_text(segments), "requests": [], "calls": [], "usage": {},
    }
    telemetry["pa_background_tools"] = trace
    summary = {"status": "failed", "outcome": None, "reason": None, "result": None}
    totals = trace["usage"]
    last_request_id = None
    executed = set()
    queried = set()
    seen_ids = set()
    query_allowed = True

    def available_tools():
        tools = (executor.available_tools() if hasattr(executor, "available_tools")
                 else executor.definitions)
        return [tool for tool in tools if query_allowed or _tool_name(tool) != "get_pa_card"]

    def append_result(call, result, *, duration_ms=0, executed_call=False):
        trace["calls"].append({
            "tool_call_id": call["id"], "name": _tool_name(call),
            "arguments": call["function"].get("arguments", ""),
            "result": deepcopy(result), "duration_ms": duration_ms,
            "executed": executed_call,
        })
        history.append({"role": "tool", "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False)})

    async def run():
        nonlocal query_allowed, last_request_id
        if type(max_rounds) is not int or max_rounds < 1 or timeout_seconds <= 0:
            raise _StopLoop("invalid_tool_budget")
        for round_no in range(max_rounds):
            tools = available_tools()
            names = {_tool_name(tool) for tool in tools}
            if not tools or (round_no == 0 and "get_pa_card" not in names):
                raise _StopLoop("required_tools_unavailable")
            choice = ({"type": "function", "function": {"name": "get_pa_card"}}
                      if round_no == 0 else "required")
            request = {
                "round": round_no, "system": as_text(segments),
                "input_messages": deepcopy(history), "tools": deepcopy(tools),
                "tool_choice": choice, "wire_messages": ordered_messages(segments, deepcopy(history)),
            }
            trace["requests"].append(request)
            request_started = perf_counter()
            text, thinking, calls, usage = [], [], [], {}
            rid = finish = None
            try:
                async for delta in provider.stream_tools(
                    system=segments, messages=deepcopy(history), tools=tools, tool_choice=choice,
                ):
                    rid = delta.request_id or rid
                    finish = delta.finish_reason or finish
                    if delta.kind == "tool_calls":
                        calls.extend(delta.tool_calls or [])
                    elif delta.kind == "usage":
                        usage = delta.usage or {}
                    elif delta.kind == "reasoning":
                        thinking.append(delta.text)
                    elif delta.kind == "content":
                        text.append(delta.text)
            except BaseException as exc:
                request["error_code"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "provider_error"
                rid = getattr(exc, "request_id", None) or rid
                raise
            finally:
                last_request_id = rid or last_request_id
                for key, value in usage.items():
                    if isinstance(value, (int, float)):
                        totals[key] = totals.get(key, 0) + value
                request.update(request_id=rid, finish_reason=finish, usage=deepcopy(usage),
                    duration_ms=int((perf_counter() - request_started) * 1000),
                    output_text="".join(text), tool_calls=deepcopy(calls))
            if not calls:
                raise _StopLoop("required_tool_decision_missing")
            if (len(calls) > 8 or any(
                not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]
                or not isinstance(call.get("function"), dict) or not _tool_name(call)
                for call in calls
            )):
                raise _StopLoop("invalid_native_tool_batch")
            ids = [call["id"] for call in calls]
            if len(set(ids)) != len(ids) or seen_ids.intersection(ids):
                raise _StopLoop("duplicate_tool_call_id")
            seen_ids.update(ids)
            assistant = {"role": "assistant", "content": "".join(text) or None, "tool_calls": deepcopy(calls)}
            if thinking:
                assistant["reasoning_content"] = "".join(thinking)
            history.append(assistant)
            stop_reason = None
            terminal = None
            # Never schedule mutations concurrently, even if the LLM supplies a
            # parallel batch. Each execute rechecks the database write boundary.
            for call_index, call in enumerate(calls):
                name = _tool_name(call)
                if stop_reason or terminal:
                    append_result(call, {"status": "blocked", "reason": "background_operation_already_finished"})
                    continue
                if round_no == 0 and (len(calls) != 1 or name != "get_pa_card"):
                    stop_reason = "initial_query_required"
                elif name not in names or name not in {_tool_name(tool) for tool in available_tools()}:
                    stop_reason = "tool_not_available"
                elif _signature(call) in (queried if name == "get_pa_card" else executed):
                    stop_reason = "repeated_identical_call_limit"
                    trace["repetition_blocked"] = True
                if stop_reason:
                    append_result(call, {"status": "blocked", "reason": stop_reason})
                    continue
                executed.add(_signature(call))
                if name == "get_pa_card":
                    queried.add(_signature(call))
                call_started = perf_counter()
                try:
                    result = await executor.execute(call)
                except BaseException as exc:
                    # A cancellation may coincide with commit completion. Do
                    # not label the write rolled back or claim it succeeded;
                    # the executor's durable receipt resolves that on retry.
                    append_result(call, {"status": "unknown", "reason":
                        "execution_cancelled" if isinstance(exc, asyncio.CancelledError) else "executor_error"},
                        duration_ms=int((perf_counter() - call_started) * 1000), executed_call=True)
                    for pending in calls[call_index + 1:]:
                        append_result(pending, {"status": "blocked", "reason": "background_operation_already_finished"})
                    raise
                append_result(call, result, duration_ms=int((perf_counter() - call_started) * 1000), executed_call=True)
                summary["result"] = deepcopy(result)
                status, reason = result.get("status"), result.get("reason")
                if reason in STALE_REASONS:
                    stop_reason = reason
                elif (name == "get_pa_card" and status == "ok"
                      and getattr(executor, "module", None) and result.get("module")
                      and result["module"] != executor.module):
                    stop_reason = "module_changed_requery_state"
                elif status == "failed":
                    stop_reason = reason or "tool_failed"
                elif status in TERMINAL_STATUSES:
                    terminal = status
                elif name == "get_pa_card" and status == "ok":
                    query_allowed = False
                elif status == "blocked":
                    query_allowed = True
                    if name != "get_pa_card":
                        queried.clear()
            if stop_reason:
                raise _StopLoop(stop_reason)
            if terminal:
                summary.update(status="completed", outcome=terminal)
                return
        trace["budget_exhausted"] = True
        raise _StopLoop("tool_budget_exhausted")

    try:
        async with timeout(timeout_seconds):
            await run()
    except _StopLoop as exc:
        summary["reason"] = exc.reason
    except (TimeoutError, asyncio.TimeoutError):
        summary["reason"] = "total_timeout"
    except asyncio.CancelledError:
        summary.update(status="cancelled", reason="cancelled")
        raise
    except ProviderError as exc:
        summary["reason"] = "provider_error"
        trace["error_type"] = type(exc).__name__
    except Exception as exc:
        summary["reason"] = "unexpected_error"
        trace["error_type"] = type(exc).__name__
    finally:
        summary.update(usage=deepcopy(totals), request_id=last_request_id,
            rounds=len(trace["requests"]), duration_ms=int((perf_counter() - started) * 1000))
        trace.update(status=summary["status"], outcome=summary["outcome"],
            error=summary["reason"], duration_ms=summary["duration_ms"],
            history=deepcopy(history))
    return summary
