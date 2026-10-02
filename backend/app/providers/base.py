"""Common interface every LLM provider implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import copy
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

from ..prompts import SystemPromptSegment
from ..schemas import Message

# A system prompt is one string, a list of strings, or a list of cache-tagged
# `SystemPromptSegment`s ordered stable-prefix-first. Providers that support
# prompt caching put a breakpoint on each cacheable segment; providers that
# don't just join the text. See prompts.build_system_segments.
SystemPrompt = str | Sequence[str] | Sequence[SystemPromptSegment]


class ProviderError(RuntimeError):
    """Raised for configuration errors or upstream API failures."""


@dataclass
class Completion:
    text: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    # Thinking-capable OpenAI-compatible providers return this separately
    # from the user-facing answer. It is deliberately not fed back into the
    # next turn's message history; it exists for this turn's optional UI.
    reasoning_content: str = ""
    finish_reason: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class StreamDelta:
    """One provider stream fragment, classified before graph transport."""

    kind: Literal["reasoning", "content", "usage", "tool_calls"]
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None
    request_id: str | None = None
    tool_calls: list[dict] | None = None


def as_segments(system: SystemPrompt) -> list[SystemPromptSegment]:
    """Normalise any accepted system-prompt shape to cache-tagged segments."""
    if isinstance(system, str):
        return [SystemPromptSegment(system)]
    return [
        part if isinstance(part, SystemPromptSegment) else SystemPromptSegment(part)
        for part in system
    ]


def as_text(system: SystemPrompt) -> str:
    return "\n\n".join(segment.text for segment in as_segments(system))


class LLMProvider(ABC):
    #: Registry key — "claude", "deepseek".
    name: str
    #: Configured model id for the main (non-router) calls. Reported back to
    #: the caller, and used when streaming, where no model id comes back.
    model: str = ""
    thinking_override: bool | None = None
    supports_native_tools: bool = False

    async def stream_tools(self, *, system, messages, tools, tool_choice="auto"):
        """Native provider protocol; never parse pseudo-tools from reply text."""
        if not self.supports_native_tools:
            raise ProviderError(f"{self.name} has no configured native tool transport")
        from .tool_calling import stream_openai_tools
        async for delta in stream_openai_tools(self, system=system, messages=messages,
                                              tools=tools, tool_choice=tool_choice):
            yield delta
    deep_reply_enabled: bool = False
    reply_effort: str | None = None

    def with_thinking(self, enabled: bool | None) -> "LLMProvider":
        # Registry clients are shared. Copy only the wrapper; never mutate its
        # settings or override for another concurrent conversation.
        scoped = copy(self)
        scoped.thinking_override = enabled
        scoped.deep_reply_enabled = False
        scoped.reply_effort = None
        return scoped

    def reply_effort_options(self) -> tuple[str, ...]:
        from ..generation_policy import reply_effort_options
        return (reply_effort_options(self._settings, self.name, model=self.model)
                if hasattr(self, "_settings") else ())

    def with_deep_reply(self, effort: str | None = None) -> "LLMProvider":
        """Increase only this wrapper's main-reply compute, not its auxiliaries."""
        if effort is not None and effort not in self.reply_effort_options():
            raise ValueError("Unsupported main reply effort for this model/channel")
        scoped = copy(self)
        scoped.deep_reply_enabled = True
        scoped.reply_effort = effort
        return scoped

    def deep_reply_policy(self) -> dict:
        """Requested wire configuration for experiment diagnostics, not a claim
        that the upstream actually spent its entire budget on reasoning.
        """
        if not self.deep_reply_enabled:
            return {}
        result = {"enabled": True, "model": self.model}
        if self.reply_effort is not None:
            result["reply_effort"] = self.reply_effort
        if hasattr(self, "_settings"):
            from ..generation_policy import deep_reply_thinking_options
            result.update(
                max_tokens=self._main_max_tokens(),
                timeout_seconds=self._main_timeout_seconds(),
                thinking_options=deep_reply_thinking_options(self._settings, self.name,
                    model=self.model, effort=self.reply_effort),
            )
        return result

    def _main_max_tokens(self) -> int:
        configured = getattr(self._settings, f"{self.name}_max_tokens")
        return max(configured, 16384) if self.deep_reply_enabled else configured

    def _main_timeout_seconds(self) -> float:
        configured = getattr(self._settings, "provider_request_timeout_seconds", 60.0)
        return max(configured, 180.0) if self.deep_reply_enabled else configured

    def _main_client(self):
        # Raising only the outer deadline would leave the SDK's shorter read
        # timeout in force. with_options shares transport without mutating it.
        return (self._client.with_options(timeout=self._main_timeout_seconds(), max_retries=0)
                if self.deep_reply_enabled else self._client)

    def _main_thinking_options(self, messages) -> dict:
        from ..generation_policy import deep_reply_thinking_options, main_thinking_options
        if self.deep_reply_enabled:
            return deep_reply_thinking_options(self._settings, self.name,
                model=self.model, effort=self.reply_effort)
        return main_thinking_options(self._settings, self.name, messages,
                                     enabled_override=self.thinking_override)

    @abstractmethod
    async def complete(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> Completion:
        """Return the full assistant reply in one shot."""

    @abstractmethod
    def stream(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> AsyncIterator[StreamDelta]:
        """Yield reasoning and user-facing text as separate deltas."""

    @abstractmethod
    async def classify(
        self, *, system: str, user: str, allowed: Sequence[str], default: str
    ) -> str:
        """Pick one label from `allowed` for `user`.

        This is the router's hot path: a small, cheap call on a fast model —
        no thinking, no streaming, a tight token cap. It must never raise.
        An unusable answer, an API error, or a timeout all return `default`,
        because a routing failure should degrade to the fallback module rather
        than take down the chat request.
        """

    @abstractmethod
    async def route(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> str:
        """Raw text from the same fast, cheap router model `classify` uses.

        For a caller that needs more than a single label — e.g. parsing a
        small JSON object back out, or a short free-text summary — but still
        wants `classify`'s cost profile (fast, non-thinking model) rather
        than a full call on the main model. `max_tokens` overrides
        `settings.router_max_tokens` for callers that need more room than a
        routing decision does (e.g. `summarizer_node`'s `SUMMARIZER_MAX_TOKENS`)
        — the router model's classification calls stay small by default.
        Never raises: returns `""` on any failure (bad response, API error,
        refusal), and it is the caller's job to treat that as "the router
        said nothing useful" and fall back accordingly.
        """

    async def route_with_reasoning(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> Completion:
        """Router completion with separately exposed model reasoning.

        Providers without a thinking-capable router inherit this honest
        fallback: the decision text is still returned and reasoning is empty.
        DeepSeek and Doubao override it with their native thinking mode.
        """
        text = await self.route(system=system, user=user, max_tokens=max_tokens)
        return Completion(text=text, model=self.model)

    async def route_detailed(
        self, *, system: str, user: str, max_tokens: int | None = None,
        include_reasoning: bool = False,
        reasoning_effort: str | None = None,
    ) -> Completion:
        """Router metadata, with optional native reasoning on the same call."""
        if include_reasoning:
            return await self.route_with_reasoning(system=system, user=user, max_tokens=max_tokens)
        text = await self.route(system=system, user=user, max_tokens=max_tokens)
        return Completion(text=text, model=self.model)


def match_label(raw: str, allowed: Sequence[str], default: str) -> str:
    """Coerce a model's free-text answer into one of `allowed`.

    Tolerates quotes, trailing punctuation, and a label wrapped in a short
    sentence. Ambiguous or unrecognised output falls back to `default`.
    """
    text = raw.strip().strip("\"'`.,;:!").lower()
    for label in allowed:
        if text == label.lower():
            return label
    hits = [label for label in allowed if label.lower() in text]
    return hits[0] if len(hits) == 1 else default
