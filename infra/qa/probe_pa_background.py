"""Synthetic live-provider PA probe; no database sessions or production writes.

Run from the configured candidate backend directory:
    PYTHONPATH=. python ../infra/qa/probe_pa_background.py

This makes paid requests to the configured background provider. All dialogue,
plan state and tool results are synthetic and held in this process. Output is
one compact JSON object with safe wire metadata, tool calls and timings.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from time import perf_counter
from types import SimpleNamespace

from app.config import get_settings
from app.generation_policy import is_ark_kimi, native_thinking_options
from app.pa_background import background_provider
from app.pa_background_loop import run_pa_background_tools
from app.pa_card_tools import definitions
from app.providers import get_provider
from app.schemas import Message


ORIGINAL_USER = "我选择散步作为核心活动，每次10分钟，地点小区里，我自己去。时间、难度和障碍还没想好，先留草稿。"
CHANGE_USER = "把散步的时长从10分钟改成15分钟，先只保存草稿，其他保持原样，暂时不要展示或确认。"


class SyntheticExecutor:
    """Real tool schemas with only synthetic, process-local state transitions."""
    module = "module_2"
    ui_enabled = False

    def __init__(self):
        self.definitions = definitions(self.module)
        self.version = 7
        self.trace = []
        self.saved_arguments = []
        self.queried = False
        self.draft = {"id": 701, "activity_content": "散步", "duration_minutes": 10,
            "location": "小区里", "companion": "独自", "record_status": "draft",
            "confirmation_status": "unconfirmed", "schedule_text": None,
            "difficulty_rating": None, "potential_barriers": None, "barrier_coping_plan": None}

    def available_tools(self):
        return self.definitions

    async def execute(self, call):
        name = call["function"]["name"]
        args = json.loads(call["function"]["arguments"])
        if name == "get_pa_card":
            self.queried = True
            result = {"status": "ok", "state_version": self.version, "module": self.module,
                "goal_id": 101, "cycle_id": 201, "draft": deepcopy(self.draft),
                "confirmed_plan": None, "unfinished_core_goals": [], "last_reviewed_goal": None,
                "recent_sources": [
                    {"message_id": 1, "role": "user", "text": ORIGINAL_USER},
                    {"message_id": 2, "role": "assistant", "text": "可以，先保留你已经想好的内容。"},
                    {"message_id": 3, "role": "user", "text": CHANGE_USER}]}
        elif name == "save_pa_card":
            data = args.get("data") or {}
            definition = next(tool["function"] for tool in self.definitions if tool["function"]["name"] == name)
            required = set(definition["parameters"]["properties"]["data"]["required"])
            if not self.queried or args.get("state_version") != self.version:
                result = {"status": "blocked", "reason": "state_changed_requery_state"}
            elif not required.issubset(data):
                result = {"status": "blocked", "reason": "invalid_arguments", "missing": sorted(required - set(data))}
            elif (data.get("target_activity_duration_minutes") != 15
                  or data.get("target_activity_content") != self.draft["activity_content"]):
                result = {"status": "blocked", "reason": "duration_change_not_applied"}
            else:
                self.saved_arguments.append(deepcopy(args))
                self.draft["duration_minutes"] = data["target_activity_duration_minutes"]
                self.version += 1
                result = {"status": "draft_saved", "confirmed": False, "record_id": self.draft["id"],
                    "state_version": self.version, "module": self.module, "saved_draft": deepcopy(self.draft)}
        elif name == "continue_pa_conversation":
            result = ({"status": "continue_conversation", "state_version": self.version, "module": self.module}
                if self.saved_arguments else {"status": "blocked", "reason": "requested_duration_change_not_saved"})
        else:
            result = {"status": "blocked", "reason": "user_requested_draft_only"}
        self.trace.append({"name": name, "arguments": deepcopy(args), "status": result["status"],
                           "reason": result.get("reason")})
        return result


class NativeOnlyProvider:
    """The probe permits native background tools and no conversational methods."""
    def __init__(self, provider):
        self.provider = provider
        self.model = provider.model
        self.requests = []
        self.foreground_calls = 0

    async def stream_tools(self, **kwargs):
        self.requests.append({"model": self.model, "tool_choice": deepcopy(kwargs["tool_choice"]),
                              "tool_count": len(kwargs["tools"])})
        async for delta in self.provider.stream_tools(**kwargs):
            yield delta

    async def complete(self, **kwargs):
        self.foreground_calls += 1
        raise AssertionError("foreground_completion_forbidden")

    async def stream(self, **kwargs):
        self.foreground_calls += 1
        raise AssertionError("foreground_stream_forbidden")
        yield  # Make misuse an async-generator failure, like the real API.


async def probe(settings, router_provider):
    started = perf_counter()
    registered_model = router_provider.model
    provider = background_provider(SimpleNamespace(settings=settings, router_provider=router_provider))
    expected_model = getattr(settings, provider.name + "_router_model")
    expected_thinking = native_thinking_options(settings, provider.name, enabled=False, model=provider.model)
    transport_requests = []
    native = NativeOnlyProvider(provider)
    executor, telemetry = SyntheticExecutor(), {}
    original_client = provider._client
    original_create = original_client.chat.completions.create

    async def observed_create(**kwargs):
        extra = kwargs.get("extra_body") or {}
        transport_requests.append({
            "model": kwargs.get("model"), "tool_choice": deepcopy(kwargs.get("tool_choice")),
            "tools": [tool["function"]["name"] for tool in kwargs.get("tools", [])],
            "max_tokens": kwargs.get("max_tokens"), "stream": kwargs.get("stream"),
            "thinking": {key: deepcopy(extra[key]) for key in
                ("enable_thinking", "thinking", "reasoning_effort", "thinking_budget") if key in extra},
        })
        return await original_create(**kwargs)

    # Replace only the per-job wrapper's client reference. The registry client,
    # its credentials and its foreground model wrapper remain untouched.
    provider._client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=observed_create)))
    result = await run_pa_background_tools(native, executor,
        system="处理用户对已有核心目标草稿的修改。只保存真实修改，不展示、不确认，不新增目标。",
        messages=[Message(role="user", content=ORIGINAL_USER),
            Message(role="assistant", content="可以，先保留你已经想好的内容。"),
            Message(role="user", content=CHANGE_USER)],
        max_rounds=settings.pa_card_tool_max_rounds,
        timeout_seconds=settings.pa_card_tool_timeout_seconds, telemetry=telemetry)
    provider._client = original_client
    requests = telemetry["pa_background_tools"]["requests"]
    checks = {
        "completed": result["status"] == "completed" and result["outcome"] == "continue_conversation",
        "application_initial_read": telemetry["pa_background_tools"].get("initial_read_source") == "application",
        "queried_first": bool(executor.trace) and executor.trace[0]["name"] == "get_pa_card",
        "saved_once": len(executor.saved_arguments) == 1 and executor.draft["duration_minutes"] == 15,
        "explicit_terminal": bool(executor.trace) and executor.trace[-1]["name"] == "continue_pa_conversation",
        "no_foreground_calls": native.foreground_calls == 0,
        "no_final_reply_request": all(request["tool_choice"] != "none" for request in requests),
        "background_wrapper": provider is not router_provider and provider.thinking_override is False
            and provider.deep_reply_enabled is False and router_provider.model == registered_model,
        "background_wire_model": bool(transport_requests)
            and all(request["model"] == expected_model for request in transport_requests),
        "background_wire_budget": bool(transport_requests)
            and all(request["max_tokens"] == getattr(settings, "pa_card_background_max_tokens", 4096)
                    for request in transport_requests),
        "background_policy_on_wire": bool(transport_requests)
            and all(request["thinking"] == expected_thinking for request in transport_requests),
        "all_requests_are_tools": len(transport_requests) == len(native.requests) == result["rounds"]
            and all(request["tools"] and request["stream"] is True for request in transport_requests),
    }
    return {
        "ok": all(checks.values()), "fixture": "synthetic_duration_10_to_15",
        "production_writes": False, "checks": checks,
        "background_reasoning_policy": {
            "mode": "mandatory_low" if is_ark_kimi(settings, provider.name, model=provider.model) else "disabled",
            "expected_wire": expected_thinking,
        },
        "result": {key: result.get(key) for key in
            ("status", "outcome", "reason", "rounds", "duration_ms", "usage")},
        "tool_calls": executor.trace, "wire_requests": transport_requests,
        "request_timings_ms": [request["duration_ms"] for request in requests],
        "suppressed_content_chars": sum(len(request.get("output_text") or "") for request in requests),
        "duration_ms": round((perf_counter() - started) * 1000),
    }


async def main():
    router_provider = None
    try:
        settings = get_settings()
        router_provider = get_provider(settings.router_provider_name)
        report = await probe(settings, router_provider)
    except Exception as exc:
        # Avoid printing SDK exceptions, request headers or configured secrets.
        report = {"ok": False, "fixture": "synthetic_duration_10_to_15",
                  "production_writes": False, "error_type": type(exc).__name__}
    finally:
        if router_provider is not None:
            await router_provider._client.close()
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
