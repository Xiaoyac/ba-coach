"""DeepSeek provider.

DeepSeek does not ship its own SDK — its documented client is the official
OpenAI SDK pointed at `https://api.deepseek.com`. The wire format is
OpenAI-compatible, so `system` is the first entry in `messages` rather than a
separate parameter, and system segments are joined into one string (there is
no prompt-cache breakpoint to place).

Current thinking-capable models return `reasoning_content` separately from
the user-facing `content`; both are preserved so the UI can disclose the
former without mixing it into the answer.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence

import openai

from ..config import Settings
from ..schemas import Message
from .deadline import timeout
from ..generation_policy import main_thinking_options
from .base import (
    Completion,
    LLMProvider,
    ProviderError,
    StreamDelta,
    SystemPrompt,
    as_text,
    match_label,
)

logger = logging.getLogger(__name__)


class DeepSeekProvider(LLMProvider):
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
        return [{"role": "system", "content": as_text(system)}] + [
            {"role": m.role, "content": m.content} for m in messages
        ]

    async def complete_without_reasoning(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> Completion:
        """One caller-bounded recovery, same model and complete original context.

        Not a router call: using its model/prompt would lose coaching context.
        Never feeds the unfinished reasoning back as evidence or retries again.
        """
        try:
            response = await self._client.with_options(max_retries=0).chat.completions.create(
                model=self.model,
                messages=self._payload(system, messages),
                max_tokens=min(self._settings.deepseek_max_tokens, 1200),
                extra_body={"thinking": {"type": "disabled"}},
            )
        except openai.OpenAIError as exc:
            raise ProviderError("DeepSeek reply recovery failed") from exc
        choice = response.choices[0]
        usage = response.usage
        return Completion(
            text=choice.message.content or "", model=response.model,
            usage={"input_tokens": usage.prompt_tokens if usage else 0,
                   "output_tokens": usage.completion_tokens if usage else 0,
                   "reasoning_tokens": 0},
            finish_reason=choice.finish_reason,
            request_id=getattr(response, "_request_id", None),
        )

    async def complete(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> Completion:
        try:
            timeout_seconds = getattr(
                self._settings, "provider_request_timeout_seconds", 60.0
            )
            async with timeout(timeout_seconds):
                response = await self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=self._settings.deepseek_max_tokens,
                    messages=self._payload(system, messages),
                    extra_body=main_thinking_options(self._settings, self.name, messages),
                )
        except (TimeoutError, asyncio.TimeoutError, openai.APITimeoutError) as exc:
            raise ProviderError(
                "DeepSeek request timed out after "
                f"{getattr(self._settings, 'provider_request_timeout_seconds', 60.0):g}s"
            ) from exc
        except openai.APIStatusError as exc:
            raise ProviderError(
                f"DeepSeek API error {exc.status_code}: {exc.message}"
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError("Could not reach the DeepSeek API") from exc

        choice = response.choices[0]
        usage = response.usage
        return Completion(
            text=choice.message.content or "",
            model=response.model,
            usage={
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
                "reasoning_tokens": (
                    getattr(
                        getattr(usage, "completion_tokens_details", None),
                        "reasoning_tokens",
                        0,
                    )
                    or 0
                ),
            },
            reasoning_content=(
                getattr(choice.message, "reasoning_content", None)
                or getattr(choice.message, "reasoning", None)
                or ""
            ),
            finish_reason=choice.finish_reason,
            request_id=getattr(response, "_request_id", None),
        )

    async def stream(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> AsyncIterator[StreamDelta]:
        stream = None
        try:
            timeout_seconds = getattr(
                self._settings, "provider_request_timeout_seconds", 60.0
            )
            async with timeout(timeout_seconds):
                stream = await self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=self._settings.deepseek_max_tokens,
                    messages=self._payload(system, messages),
                    stream=True,
                    stream_options={"include_usage": True},
                    extra_body=main_thinking_options(self._settings, self.name, messages),
                )
                usage_payload: dict[str, int] = {}
                finish_reason: str | None = None
                async for chunk in stream:
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
                    request_id=getattr(stream, "_request_id", None),
                )
        except (TimeoutError, asyncio.TimeoutError, openai.APITimeoutError) as exc:
            raise ProviderError(
                "DeepSeek stream timed out after "
                f"{getattr(self._settings, 'provider_request_timeout_seconds', 60.0):g}s"
            ) from exc
        except openai.APIStatusError as exc:
            raise ProviderError(
                f"DeepSeek API error {exc.status_code}: {exc.message}"
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError("Could not reach the DeepSeek API") from exc

        finally:
            if stream is not None and callable(getattr(stream, "close", None)):
                await stream.close()

    async def _router_completion(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> Completion:
        """Raw text from the small, fast router model, shared by `classify`
        and `route`. Never raises — returns `""` on any failure."""
        try:
            timeout_seconds = getattr(
                self._settings, "router_request_timeout_seconds", 12.0
            )
            if (max_tokens or 0) > 512:
                timeout_seconds = getattr(self._settings, "background_model_timeout_seconds", 30.0)
            async with timeout(timeout_seconds):
                response = await self._client.chat.completions.create(
                    model=self._settings.deepseek_router_model,
                    max_tokens=max_tokens or self._settings.router_max_tokens,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    extra_body={"thinking": {"type": "disabled"}},
                )
            choice = response.choices[0]
            usage = response.usage
            return Completion(
                text=choice.message.content or "",
                model=response.model,
                usage={
                    "input_tokens": usage.prompt_tokens if usage else 0,
                    "output_tokens": usage.completion_tokens if usage else 0,
                    "reasoning_tokens": 0,
                },
                finish_reason=choice.finish_reason,
                request_id=getattr(response, "_request_id", None),
            )
        except Exception:  # noqa: BLE001 — routing must never break the turn
            logger.warning("DeepSeek router call failed", exc_info=True)
            return Completion(text="", model=self._settings.deepseek_router_model)

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
        if reasoning_effort == "disabled":
            return await self._router_completion(system=system, user=user, max_tokens=max_tokens)
        try:
            # A bounded mediator request must not spend its deadline on SDK retries.
            client = self._client.with_options(max_retries=0) if reasoning_effort is not None else self._client
            timeout_seconds = getattr(
                self._settings, "background_model_timeout_seconds", 30.0
            )
            qwen_compatible = (
                model.casefold().startswith("qwen")
                or "dashscope.aliyuncs.com" in str(
                    getattr(self._settings, "deepseek_base_url", "") or ""
                ).casefold()
            )
            thinking_body = (
                {"enable_thinking": True,
                 "thinking_budget": int(getattr(self._settings, "qwen_thinking_budget", 1024))}
                if qwen_compatible else {"thinking": {"type": "enabled"}}
            )
            request_options = {}
            if reasoning_effort and not qwen_compatible:
                request_options["reasoning_effort"] = reasoning_effort
            async with timeout(timeout_seconds):
                response = await client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens or self._settings.router_reasoning_max_tokens,
                    **request_options,
                    messages=[
                        {"role": "system", "content": system},
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
                usage={
                    "input_tokens": usage.prompt_tokens if usage else 0,
                    "output_tokens": usage.completion_tokens if usage else 0,
                    "reasoning_tokens": (
                        getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", 0)
                        or 0
                    ),
                },
                finish_reason=choice.finish_reason,
                request_id=getattr(response, "_request_id", None),
            )
        except Exception:  # noqa: BLE001 — routing must never break the turn
            logger.warning("DeepSeek thinking router call failed", exc_info=True)
            return Completion(text="", model=model)
