"""Claude provider — official Anthropic Python SDK (`anthropic`)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence

import anthropic

from ..config import Settings
from ..schemas import Message
from .base import (
    Completion,
    LLMProvider,
    ProviderError,
    StreamDelta,
    SystemPrompt,
    as_segments,
    match_label,
)

logger = logging.getLogger(__name__)


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise ProviderError(
                "ANTHROPIC_API_KEY is not set — add it to backend/.env"
            )
        self._settings = settings
        self.model = settings.claude_model
        # AsyncAnthropic also picks the key up from the environment on its own;
        # passing it explicitly keeps the failure mode above readable.
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    def _system_blocks(self, system: SystemPrompt) -> list[dict]:
        """Turn system segments into cache-annotated text blocks.

        A breakpoint goes on every segment marked cacheable. In practice that
        is the global prompt (identical across all four modules, so the entry
        is reused whichever module the router picks) and the module prompt
        (stable for every turn inside that module). Volatile trailing content —
        retrieved knowledge, memory, session context — is deliberately left
        unmarked: it changes each turn, so a breakpoint there would be written
        once and never read.

        The API allows at most 4 breakpoints; if more segments are marked, the
        earliest are dropped, since a later breakpoint's prefix covers them.
        """
        segments = as_segments(system)
        cacheable = [i for i, segment in enumerate(segments) if segment.cacheable]
        marked = set(cacheable[-4:])

        blocks: list[dict] = []
        for index, segment in enumerate(segments):
            block: dict = {"type": "text", "text": segment.text}
            if index in marked:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        return blocks

    def _request_kwargs(self, system: SystemPrompt, messages: list[Message]) -> dict:
        return {
            "model": self.model,
            "max_tokens": self._settings.claude_max_tokens,
            "system": self._system_blocks(system),
            # Adaptive thinking: Claude decides per turn how much to reason.
            # Depth/cost is tuned with `effort`, not a token budget.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._settings.claude_effort},
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }

    async def complete(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> Completion:
        try:
            response = await self._client.messages.create(
                **self._request_kwargs(system, messages)
            )
        except anthropic.APIStatusError as exc:  # 4xx / 5xx from the API
            raise ProviderError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Could not reach the Claude API") from exc

        # Safety classifiers can decline a request: HTTP 200, but `content` is
        # empty or partial. Check stop_reason before reading content.
        if response.stop_reason == "refusal":
            raise ProviderError(
                "The model declined this request. Rephrase, or route it to a "
                "human. (stop_reason=refusal)"
            )

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        return Completion(
            text=text,
            model=response.model,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_input_tokens": response.usage.cache_read_input_tokens or 0,
            },
        )

    async def stream(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> AsyncIterator[StreamDelta]:
        try:
            async with self._client.messages.stream(
                **self._request_kwargs(system, messages)
            ) as stream:
                async for chunk in stream.text_stream:
                    yield StreamDelta(kind="content", text=chunk)
                final = await stream.get_final_message()
                if final.stop_reason == "refusal":
                    raise ProviderError("The model declined this request.")
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Could not reach the Claude API") from exc

    async def _router_completion(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> str:
        """Raw text from the small, fast router model, shared by `classify`
        and `route`.

        No `thinking` and no `output_config` here on purpose: those parameters
        are model-gated (Haiku 4.5 rejects `effort` outright), and routing
        wants the cheapest possible call. CLAUDE_ROUTER_MODEL should stay a
        fast non-thinking model — point it at a thinking model and the reply
        can be swallowed by the token cap, which silently degrades to the
        caller's own fallback rather than erroring.

        Never raises — returns `""` on any failure, mirroring `route`'s
        contract; `classify` turns that into `default` itself.
        """
        try:
            response = await self._client.messages.create(
                model=self._settings.claude_router_model,
                max_tokens=max_tokens or self._settings.router_max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            if response.stop_reason == "refusal":
                return ""
            return "".join(
                block.text for block in response.content if block.type == "text"
            )
        except Exception:  # noqa: BLE001 — routing must never break the turn
            logger.warning("Claude router call failed", exc_info=True)
            return ""

    async def classify(
        self, *, system: str, user: str, allowed: Sequence[str], default: str
    ) -> str:
        raw = await self._router_completion(system=system, user=user)
        return match_label(raw, allowed, default) if raw else default

    async def route(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> str:
        return await self._router_completion(system=system, user=user, max_tokens=max_tokens)
