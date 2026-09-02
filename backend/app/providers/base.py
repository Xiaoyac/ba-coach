"""Common interface every LLM provider implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
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

    kind: Literal["reasoning", "content", "usage"]
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None
    request_id: str | None = None


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
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> Completion:
        """Non-thinking router call with usage/request metadata when available."""
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
