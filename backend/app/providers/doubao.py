"""Doubao provider through Volcengine Ark's OpenAI-compatible endpoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence

import openai

from ..config import Settings
from ..schemas import Message
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


def _api_error_message(exc: openai.APIStatusError) -> str:
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    code = error.get("code") if isinstance(error, dict) else None
    if exc.status_code == 429 and code == "SetLimitExceeded":
        return (
            "豆包模型已达到安全体验模式的推理额度上限，服务已暂停。"
            "请联系管理员在火山方舟“模型开通”页面检查并调整安全体验额度，"
            "或切换其他可用模型。调整额度可能产生额外费用。"
        )
    return f"Doubao API error {exc.status_code}: {exc.message}"


class DoubaoProvider(LLMProvider):
    name = "doubao"

    def __init__(self, settings: Settings) -> None:
        if not settings.doubao_api_key or not settings.doubao_model:
            raise ProviderError(
                "豆包尚未配置，请在 backend/.env 设置 DOUBAO_API_KEY 和 DOUBAO_MODEL"
            )
        self._settings = settings
        self.model = settings.doubao_model
        self._client = openai.AsyncOpenAI(
            api_key=settings.doubao_api_key,
            base_url=settings.doubao_base_url,
        )

    @staticmethod
    def _payload(system: SystemPrompt, messages: list[Message]) -> list[dict]:
        return [{"role": "system", "content": as_text(system)}] + [
            {"role": message.role, "content": message.content} for message in messages
        ]

    async def complete(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> Completion:
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                max_tokens=self._settings.doubao_max_tokens,
                messages=self._payload(system, messages),
                extra_body={
                    "thinking": {"type": "enabled"},
                    "reasoning_effort": self._settings.doubao_reasoning_effort,
                },
            )
        except openai.APIStatusError as exc:
            raise ProviderError(_api_error_message(exc)) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError("Could not reach the Doubao API") from exc

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
            reasoning_content=getattr(choice.message, "reasoning_content", None) or "",
            finish_reason=choice.finish_reason,
            request_id=getattr(response, "_request_id", None),
        )

    async def stream(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> AsyncIterator[StreamDelta]:
        stream = None
        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                max_tokens=self._settings.doubao_max_tokens,
                messages=self._payload(system, messages),
                stream=True,
                stream_options={"include_usage": True},
                extra_body={
                    "thinking": {"type": "enabled"},
                    "reasoning_effort": self._settings.doubao_reasoning_effort,
                },
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
                reasoning = getattr(delta, "reasoning_content", None)
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
        except openai.APIStatusError as exc:
            raise ProviderError(_api_error_message(exc)) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError("Could not reach the Doubao API") from exc

        finally:
            if stream is not None and callable(getattr(stream, "close", None)):
                await stream.close()

    async def _router_completion(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> Completion:
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.doubao_router_model or self.model,
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
        except Exception:  # noqa: BLE001 - routing must never break a turn
            logger.warning("Doubao router call failed", exc_info=True)
            return Completion(text="", model=self._settings.doubao_router_model or self.model)

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
        """Thinking-enabled call used only by the post-hoc module router."""
        model = self._settings.doubao_router_model or self.model
        try:
            client = self._client.with_options(max_retries=0) if reasoning_effort is not None else self._client
            response = await client.chat.completions.create(
                model=model,
                max_tokens=max_tokens or self._settings.router_reasoning_max_tokens,
                **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                extra_body={"thinking": {"type": "enabled"}},
            )
            choice = response.choices[0]
            usage = response.usage
            return Completion(
                text=choice.message.content or "",
                model=response.model,
                reasoning_content=(
                    getattr(choice.message, "reasoning_content", None) or ""
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
            logger.warning("Doubao thinking router call failed", exc_info=True)
            return Completion(text="", model=model)
