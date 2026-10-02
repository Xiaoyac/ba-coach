"""OpenAI-compatible provider, currently configured for Qwen3.8-Flash.

The internal ``deepseek`` key and environment variable names are retained for
existing accounts and deployments. Requests use the configured URL/model and
model-specific thinking parameters. Main-reply context can preserve a trusted
system suffix after history; ordinary/router callers retain their old layout.

Current thinking-capable models return `reasoning_content` separately from
the user-facing `content`; both are preserved so the UI can disclose the
former without mixing it into the answer.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence

import openai

from ..config import Settings
from ..schemas import Message
from .deadline import timeout
from .request_ids import request_id as extract_request_id
from ..generation_policy import main_thinking_options, native_thinking_options
from .prompt_cache import system_messages
from .usage import openai_usage
from ..generation_policy import is_ark_kimi, ark_kimi_effort, auxiliary_output_budget
from .base import (
    Completion,
    LLMProvider,
    ProviderError,
    StreamDelta,
    SystemPrompt,
    match_label,
)

logger = logging.getLogger(__name__)


def _cache_usage(usage: object) -> dict[str, int]:
    """Preserve reported cache counters; an absent counter is not a cache miss."""
    details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage, Mapping)
        else getattr(usage, "prompt_tokens_details", None)
    )
    counters: dict[str, int] = {}
    for source, target in (
        ("cached_tokens", "cache_read_input_tokens"),
        ("cache_creation_input_tokens", "cache_creation_input_tokens"),
    ):
        value = details.get(source) if isinstance(details, Mapping) else getattr(details, source, None)
        if type(value) is int and value >= 0:
            counters[target] = value
    return counters


def _stream_header_request_id(stream: object) -> str | None:
    """AsyncStream exposes the HTTP response, not a parsed _request_id field."""
    headers = getattr(getattr(stream, "response", None), "headers", None)
    value = headers.get("x-request-id") if headers is not None else None
    return value if isinstance(value, str) and value else None


def _error_request_id(error: Exception) -> str | None:
    # APIStatusError obtains this from the response header; body completion IDs
    # such as chatcmpl-* are not substitutes for a provider request ID.
    if isinstance(error, openai.APIStatusError):
        value = error.request_id
        return value if isinstance(value, str) and value else None
    return None


def _provider_error(message: str, request_id: str | None = None) -> ProviderError:
    error = ProviderError(message)
    if request_id:
        error.request_id = request_id
    return error


class DeepSeekProvider(LLMProvider):
    supports_native_tools = True
    name = "deepseek"

    def __init__(self, settings: Settings) -> None:
        if not settings.deepseek_api_key:
            raise ProviderError(
                "DEEPSEEK_API_KEY is not set — add it to backend/.env"
            )
        self._settings = settings
        self.model = settings.deepseek_model
        self._client = openai.AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.provider_request_timeout_seconds,
            max_retries=settings.provider_max_retries,
        )

    def _payload(self, system: SystemPrompt, messages: list[Message]) -> list[dict]:
        from .prompt_cache import ordered_messages
        from .base import as_segments
        if not any(s.after_history for s in as_segments(system)):
            return system_messages(system, settings=self._settings, model=self.model) + [
                {"role": m.role, "content": m.content} for m in messages]
        return ordered_messages(system, [{"role": m.role, "content": m.content} for m in messages])

    @property
    def supports_tail_system(self) -> bool:
        # Verified on the configured Ark K3 channel; other transports retain
        # their existing layout until separately tested.
        return is_ark_kimi(self._settings, self.name, model=self.model)

    async def complete_without_reasoning(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> Completion:
        """One caller-bounded recovery, same model and complete original context.

        Not a router call: using its model/prompt would lose coaching context.
        Never feeds the unfinished reasoning back as evidence or retries again.
        """
        if self.deep_reply_enabled:
            # The experiment must not silently downgrade a failed deep reply.
            # This remains one caller-bounded recovery on the same full input.
            return await self.complete(system=system, messages=messages)
        # An explicit administrator choice applies even to a bounded recovery.
        enabled = self.thinking_override is True
        try:
            response = await self._client.with_options(max_retries=0).chat.completions.create(
                model=self.model,
                messages=self._payload(system, messages),
                max_tokens=auxiliary_output_budget(
                    self._settings, self.name, self.model,
                    min(self._settings.deepseek_max_tokens, 1200 + (self._settings.qwen_thinking_budget if enabled else 0))),
                extra_body=native_thinking_options(self._settings, self.name, enabled=enabled, model=self.model),
            )
        except openai.OpenAIError as exc:
            raise _provider_error(f"{self.model} reply recovery failed", _error_request_id(exc)) from exc
        choice = response.choices[0]
        usage = response.usage
        return Completion(
            text=choice.message.content or "", model=response.model,
            usage={"input_tokens": usage.prompt_tokens if usage else 0,
                   "output_tokens": usage.completion_tokens if usage else 0,
                   "reasoning_tokens": (getattr(getattr(usage, "completion_tokens_details", None),
                                                "reasoning_tokens", 0) or 0) if enabled else 0,
                   **_cache_usage(usage)},
            reasoning_content=(getattr(choice.message, "reasoning_content", None)
                               or getattr(choice.message, "reasoning", None) or ""),
            finish_reason=choice.finish_reason,
            request_id=extract_request_id(response),
        )

    async def complete(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> Completion:
        try:
            timeout_seconds = self._main_timeout_seconds()
            async with timeout(timeout_seconds):
                response = await self._main_client().chat.completions.create(
                    model=self.model,
                    max_tokens=auxiliary_output_budget(
                        self._settings, self.name, self.model, self._main_max_tokens()),
                    messages=self._payload(system, messages),
                    extra_body=self._main_thinking_options(messages),
                )
        except (TimeoutError, asyncio.TimeoutError, openai.APITimeoutError) as exc:
            raise ProviderError(
                f"{self.model} request timed out after "
                f"{self._main_timeout_seconds():g}s"
            ) from exc
        except openai.APIStatusError as exc:
            raise _provider_error(
                f"{self.model} API error {exc.status_code}: {exc.message}", _error_request_id(exc)
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError(f"Could not reach the {self.model} API") from exc

        choice = response.choices[0]
        usage = response.usage
        return Completion(
            text=choice.message.content or "",
            model=response.model,
            usage=openai_usage(usage),
            reasoning_content=(
                getattr(choice.message, "reasoning_content", None)
                or getattr(choice.message, "reasoning", None)
                or ""
            ),
            finish_reason=choice.finish_reason,
            request_id=extract_request_id(response),
        )

    async def stream(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> AsyncIterator[StreamDelta]:
        stream = None
        request_id: str | None = None
        try:
            timeout_seconds = self._main_timeout_seconds()
            async with timeout(timeout_seconds):
                stream = await self._main_client().chat.completions.create(
                    model=self.model,
                    max_tokens=auxiliary_output_budget(
                        self._settings, self.name, self.model, self._main_max_tokens()),
                    messages=self._payload(system, messages),
                    stream=True,
                    stream_options={"include_usage": True},
                    extra_body=self._main_thinking_options(messages),
                )
                header_request_id = _stream_header_request_id(stream)
                request_id = header_request_id or extract_request_id(stream)
                if header_request_id:
                    # Send the identifier before content so interrupted streams
                    # can still be located in the provider's inference logs.
                    yield StreamDelta(kind="usage", request_id=header_request_id)
                usage_payload: dict[str, int] = {}
                finish_reason: str | None = None
                async for chunk in stream:
                    chunk_request_id = extract_request_id(chunk)
                    if chunk_request_id and not request_id:
                        request_id = chunk_request_id
                        yield StreamDelta(kind="usage", request_id=request_id)
                    if chunk.usage is not None:
                        usage_payload = {
                            "input_tokens": chunk.usage.prompt_tokens or 0,
                            "output_tokens": chunk.usage.completion_tokens or 0,
                            "reasoning_tokens": (
                                getattr(
                                    getattr(chunk.usage, "completion_tokens_details", None),
                                    "reasoning_tokens",
                                    0,
                                )
                                or 0
                            ),
                            **_cache_usage(chunk.usage),
                        }
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    finish_reason = choice.finish_reason or finish_reason
                    delta = choice.delta
                    reasoning = (
                        getattr(delta, "reasoning_content", None)
                        or getattr(delta, "reasoning", None)
                    )
                    if reasoning:
                        yield StreamDelta(kind="reasoning", text=reasoning)
                    if delta and delta.content:
                        yield StreamDelta(kind="content", text=delta.content)
                yield StreamDelta(
                    kind="usage",
                    usage=usage_payload,
                    finish_reason=finish_reason,
                    request_id=request_id,
                )
        except (TimeoutError, asyncio.TimeoutError, openai.APITimeoutError) as exc:
            raise _provider_error(
                f"{self.model} stream timed out after "
                f"{self._main_timeout_seconds():g}s", request_id
            ) from exc
        except openai.APIStatusError as exc:
            error_request_id = _error_request_id(exc) or request_id
            if error_request_id and error_request_id != request_id:
                yield StreamDelta(kind="usage", request_id=error_request_id)
            raise _provider_error(
                f"{self.model} API error {exc.status_code}: {exc.message}", error_request_id
            ) from exc
        except openai.APIConnectionError as exc:
            raise _provider_error(f"Could not reach the {self.model} API", request_id) from exc

        finally:
            if stream is not None and callable(getattr(stream, "close", None)):
                await stream.close()

    async def _router_completion(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> Completion:
        """Raw text from the small, fast router model, shared by `classify`
        and `route`. Never raises — returns `""` on any failure."""
        if self.thinking_override is True:
            return await self.route_with_reasoning(system=system, user=user, max_tokens=max_tokens)
        try:
            timeout_seconds = getattr(
                self._settings, "router_request_timeout_seconds", 12.0
            )
            if (max_tokens or 0) > 512:
                timeout_seconds = getattr(self._settings, "background_model_timeout_seconds", 30.0)
            async with timeout(timeout_seconds):
                response = await self._client.chat.completions.create(
                    model=self._settings.deepseek_router_model,
                    max_tokens=auxiliary_output_budget(
                        self._settings, self.name, self._settings.deepseek_router_model,
                        max_tokens or self._settings.router_max_tokens),
                    temperature=0,
                    messages=[
                        *system_messages(system, settings=self._settings, model=self._settings.deepseek_router_model),
                        {"role": "user", "content": user},
                    ],
                    extra_body=native_thinking_options(self._settings, self.name, enabled=False,
                                                       model=self._settings.deepseek_router_model),
                )
            choice = response.choices[0]
            usage = response.usage
            return Completion(
                text=choice.message.content or "",
                model=response.model,
                usage=openai_usage(usage),
                reasoning_content=(getattr(choice.message, "reasoning_content", None)
                                   or getattr(choice.message, "reasoning", None) or ""),
                finish_reason=choice.finish_reason,
                request_id=extract_request_id(response),
            )
        except Exception as exc:  # noqa: BLE001 — routing must never break the turn
            logger.warning("DeepSeek router call failed", exc_info=True)
            return Completion(text="", model=self._settings.deepseek_router_model,
                              request_id=_error_request_id(exc))

    async def classify(
        self, *, system: str, user: str, allowed: Sequence[str], default: str
    ) -> str:
        raw = (await self._router_completion(system=system, user=user)).text
        return match_label(raw, allowed, default) if raw else default

    async def route(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> str:
        return (await self._router_completion(system=system, user=user, max_tokens=max_tokens)).text

    async def route_detailed(
        self, *, system: str, user: str, max_tokens: int | None = None,
        include_reasoning: bool = False,
        reasoning_effort: str | None = None,
    ) -> Completion:
        if include_reasoning:
            return await self.route_with_reasoning(system=system, user=user, max_tokens=max_tokens, reasoning_effort=reasoning_effort)
        return await self._router_completion(system=system, user=user, max_tokens=max_tokens)

    async def route_with_reasoning(
        self, *, system: str, user: str, max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Completion:
        """Native thinking; mediator may lower effort without changing router defaults."""
        model = self._settings.deepseek_router_model
        if reasoning_effort is None:
            reasoning_effort = getattr(self._settings, "module_router_reasoning_effort", None)
        if reasoning_effort == "provider_default":
            reasoning_effort = None
        if self.thinking_override is False or (reasoning_effort == "disabled" and self.thinking_override is None):
            return await self._router_completion(system=system, user=user, max_tokens=max_tokens)
        if self.thinking_override is True:
            if reasoning_effort == "disabled":
                reasoning_effort = None
            # Auxiliary JSON/text needs output room in addition to native reasoning.
            max_tokens = max(max_tokens or 0, self._settings.router_reasoning_max_tokens) + int(
                getattr(self._settings, "qwen_thinking_budget", 1024))
        try:
            # A bounded mediator request must not spend its deadline on SDK retries.
            client = self._client.with_options(max_retries=0) if reasoning_effort is not None else self._client
            timeout_seconds = getattr(
                self._settings, "background_model_timeout_seconds", 30.0
            )
            thinking_body = native_thinking_options(self._settings, self.name, enabled=True, model=model)
            request_options = {}
            if is_ark_kimi(self._settings, self.name, model=model):
                thinking_body = {"reasoning_effort": ark_kimi_effort(reasoning_effort)}
            elif reasoning_effort and "enable_thinking" not in thinking_body:
                request_options["reasoning_effort"] = reasoning_effort
            async with timeout(timeout_seconds):
                response = await client.chat.completions.create(
                    model=model,
                    max_tokens=auxiliary_output_budget(
                        self._settings, self.name, model,
                        max_tokens or self._settings.router_reasoning_max_tokens),
                    **request_options,
                    messages=[
                        *system_messages(system, settings=self._settings, model=model),
                        {"role": "user", "content": user},
                    ],
                    extra_body=thinking_body,
                )
            choice = response.choices[0]
            usage = response.usage
            return Completion(
                text=choice.message.content or "",
                model=response.model,
                reasoning_content=(
                    getattr(choice.message, "reasoning_content", None)
                    or getattr(choice.message, "reasoning", None)
                    or ""
                ),
                usage=openai_usage(usage),
                finish_reason=choice.finish_reason,
                request_id=extract_request_id(response),
            )
        except Exception as exc:  # noqa: BLE001 — routing must never break the turn
            logger.warning("DeepSeek thinking router call failed", exc_info=True)
            return Completion(text="", model=model, request_id=_error_request_id(exc))
