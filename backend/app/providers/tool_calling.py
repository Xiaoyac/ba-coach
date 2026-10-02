"""Native OpenAI-compatible tool stream, including fragmented calls and IDs."""
import asyncio
import openai

from .base import StreamDelta, ProviderError, as_text
from .deadline import timeout
from .request_ids import request_id
from ..generation_policy import native_thinking_options
from .prompt_cache import ordered_messages


async def stream_openai_tools(provider, *, system, messages, tools, tool_choice="auto"):
    settings = provider._settings
    seconds = settings.provider_request_timeout_seconds
    # Preserve explicit per-conversation thinking choice. Tool traces must retain
    # reasoning_content when the endpoint requires it on continuation requests.
    enabled = provider.thinking_override
    if enabled is None:
        from ..generation_policy import main_thinking_options
        from ..schemas import Message
        ordinary = [Message(role=m["role"], content=m.get("content") or "")
                    for m in messages if m["role"] in {"user", "assistant"}]
        extra = main_thinking_options(settings, provider.name, ordinary)
    else:
        extra = native_thinking_options(settings, provider.name, enabled=enabled, model=provider.model)
    calls, finish, rid, usage = {}, None, None, {}
    try:
        async with timeout(seconds):
            stream = await provider._client.chat.completions.create(
                model=provider.model,
                messages=ordered_messages(system, messages),
                tools=tools, tool_choice=tool_choice,
                max_tokens=getattr(settings, provider.name + "_max_tokens"),
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
    yield StreamDelta(kind="usage", usage=usage, request_id=rid, finish_reason=finish)
    if calls:
        if finish != "tool_calls" or any(not c["id"] or not c["function"]["name"] for c in calls.values()):
            raise ProviderError("Incomplete native tool call; no operation executed")
        yield StreamDelta(kind="tool_calls", tool_calls=[calls[i] for i in sorted(calls)])
