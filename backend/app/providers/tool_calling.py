"""Native OpenAI-compatible tool stream, including fragmented calls and IDs."""
import asyncio
import openai

from .base import StreamDelta, ProviderError, as_text
from .deadline import timeout
from .request_ids import request_id
from ..generation_policy import native_thinking_options, is_ark_kimi
from .prompt_cache import ordered_messages


async def stream_openai_tools(provider, *, system, messages, tools, tool_choice="auto"):
    settings = provider._settings
    deep = getattr(provider, "deep_reply_enabled", False)
    seconds = provider._main_timeout_seconds() if deep else settings.provider_request_timeout_seconds
    client = provider._main_client() if deep else provider._client
    max_tokens = provider._main_max_tokens() if deep else getattr(settings, provider.name + "_max_tokens")
    # Background card work has its own bounded output/deadline and must not
    # inherit the user's foreground max-effort or deep-reply budget.
    seconds = getattr(provider, "tool_timeout_seconds", seconds)
    max_tokens = getattr(provider, "tool_max_tokens", max_tokens)
    forced_name = None
    if isinstance(tool_choice, dict) and is_ark_kimi(settings, provider.name, model=provider.model):
        # Ark K3 rejects named choice with HTTP 400, but accepts required.
        # Restrict the offered tools to preserve the exact requested action.
        forced_name = (tool_choice.get("function") or {}).get("name")
        selected = [t for t in tools if t.get("function", {}).get("name") == forced_name]
        if not forced_name or len(selected) != 1:
            raise ProviderError("Requested native tool is not uniquely available")
        tools, tool_choice = selected, "required"
    # Preserve explicit per-conversation thinking choice. Tool traces must retain
    # reasoning_content when the endpoint requires it on continuation requests.
    enabled = provider.thinking_override
    if deep:
        extra = provider._main_thinking_options([])
    elif enabled is None:
        from ..generation_policy import main_thinking_options
        from ..schemas import Message
        ordinary = [Message(role=m["role"], content=m.get("content") or "")
                    for m in messages if m["role"] in {"user", "assistant"}]
        extra = main_thinking_options(settings, provider.name, ordinary)
    else:
        extra = native_thinking_options(settings, provider.name, enabled=enabled, model=provider.model)
    calls, finish, rid, usage = {}, None, None, {}
    refused = False
    try:
        async with timeout(seconds):
            stream = await client.chat.completions.create(
                model=provider.model,
                messages=ordered_messages(system, messages),
                tools=tools, tool_choice=tool_choice,
                max_tokens=max_tokens,
                extra_body=extra, stream=True, stream_options={"include_usage": True},
            )
            rid = request_id(stream) or rid
            try:
                async for chunk in stream:
                    rid = request_id(chunk) or rid
                    if chunk.usage:
                        u = chunk.usage
                        usage = {"input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens,
                                 "reasoning_tokens": getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) or 0}
                        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", None)
                        if type(cached) is int:
                            usage["cache_read_input_tokens"] = cached
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    finish = choice.finish_reason or finish
                    d = choice.delta
                    if getattr(d, "refusal", None):
                        refused = True
                    if getattr(d, "reasoning_content", None):
                        yield StreamDelta(kind="reasoning", text=d.reasoning_content)
                    if d.content:
                        yield StreamDelta(kind="content", text=d.content)
                    for call in d.tool_calls or []:
                        target = calls.setdefault(call.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if call.id:
                            target["id"] = call.id
                        if call.function:
                            target["function"]["name"] += call.function.name or ""
                            target["function"]["arguments"] += call.function.arguments or ""
            finally:
                await stream.close()
    except (TimeoutError, asyncio.TimeoutError, openai.OpenAIError) as exc:
        error = ProviderError(f"{provider.name} native tool request failed ({type(exc).__name__})")
        error.request_id = rid or request_id(exc)
        raise error from exc
    yield StreamDelta(kind="usage", usage=usage, request_id=rid, finish_reason="refusal" if refused else finish)
    if calls:
        if finish != "tool_calls" or any(not c["id"] or not c["function"]["name"] for c in calls.values()):
            raise ProviderError("Incomplete native tool call; no operation executed")
        if forced_name and any(c["function"]["name"] != forced_name for c in calls.values()):
            raise ProviderError("Provider returned an unrequested native tool; no operation executed")
        yield StreamDelta(kind="tool_calls", tool_calls=[calls[i] for i in sorted(calls)])
