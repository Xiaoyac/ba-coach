"""Keep provider thinking and user-visible replies in their proper channels.

Some OpenAI-compatible reasoning endpoints occasionally emit their internal
chat-template markers literally.  Two malformed shapes have been observed in
production:

* ``content`` contains ``<thinking>...</thinking>`` followed by the answer;
* ``reasoning_content`` starts with the provider marker ``response`` and is
  actually a second answer candidate rather than a thought trace.

Raw provider fields are therefore evidence, not a trustworthy display
contract.  This module repairs the known shapes deterministically and never
lets a literal thinking block become the assistant's visible reply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_THINKING_BLOCK = re.compile(
    r"<(?P<tag>thinking|think)>\s*(?P<body>.*?)\s*</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_THINKING_OPEN = re.compile(r"<(?:thinking|think)>\s*", flags=re.IGNORECASE)
_RESPONSE_PREFIX = re.compile(
    r"^\s*(?:<response>\s*|response(?=\s|:|：|[\u3400-\u9fff])\s*[:：]?\s*)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class NormalizedReasoning:
    reply: str
    reasoning: str
    repaired: bool = False


def _response_candidate(text: str) -> str | None:
    matched = _RESPONSE_PREFIX.match(text or "")
    if not matched:
        return None
    candidate = (text or "")[matched.end() :].strip()
    return candidate or None


def normalize_reasoning_channels(
    reply: str | None,
    reasoning: str | None,
) -> NormalizedReasoning:
    """Return a safe visible reply and a genuine provider thought trace.

    The function is intentionally narrow: it repairs provider protocol
    markers, but does not pretend to judge whether a model's reasoning is
    factually correct.  If the provider put an answer candidate in
    ``reasoning_content`` while also supplying a normal visible answer, the
    duplicate candidate is discarded rather than mislabelled as reasoning.
    """

    visible = reply or ""
    thought = reasoning or ""
    repaired = False

    blocks = [match.group("body").strip() for match in _THINKING_BLOCK.finditer(visible)]
    if blocks:
        visible = _THINKING_BLOCK.sub("", visible).strip()
        thought = "\n\n".join(block for block in blocks if block)
        repaired = True
    else:
        # Never disclose a truncated/unclosed thinking block as the reply.
        opening = _THINKING_OPEN.search(visible)
        if opening:
            safe_prefix = visible[: opening.start()].strip()
            embedded = visible[opening.end() :].strip()
            candidate = _response_candidate(thought)
            visible = safe_prefix or candidate or ""
            thought = embedded
            repaired = True

    response_candidate = _response_candidate(thought)
    if response_candidate is not None:
        # A separate visible answer wins.  Only use the candidate when the
        # provider supplied no other answer at all.
        if not visible.strip():
            visible = response_candidate
        thought = ""
        repaired = True

    # Providers sometimes wrap the *reasoning field itself* in tags.  Unwrap
    # it for display, but do not move it into the visible reply.
    thought_match = _THINKING_BLOCK.fullmatch(thought.strip())
    if thought_match:
        thought = thought_match.group("body").strip()
        repaired = True

    return NormalizedReasoning(
        reply=visible.strip(),
        reasoning=thought.strip(),
        repaired=repaired,
    )


@dataclass
class ThinkingTagStreamGuard:
    """Hold only streams that may start with a literal thinking tag.

    Ordinary prose is released after a handful of prefix characters, so the
    normal fast first-token experience remains.  If the prefix is
    ``<thinking>``/``<think>``, the malformed payload is held until the final
    channel normaliser can separate it safely.
    """

    raw_parts: list[str]
    prefix_parts: list[str]
    mode: str = "deciding"  # deciding | passthrough | blocked

    @classmethod
    def create(cls) -> "ThinkingTagStreamGuard":
        return cls(raw_parts=[], prefix_parts=[])

    def push(self, delta: str) -> list[str]:
        self.raw_parts.append(delta)
        if self.mode == "passthrough":
            return [delta]
        if self.mode == "blocked":
            return []

        self.prefix_parts.append(delta)
        preview = "".join(self.prefix_parts).lstrip().lower()
        if not preview:
            return []
        markers = ("<thinking>", "<think>")
        if any(marker.startswith(preview) for marker in markers):
            return []
        if preview.startswith(markers):
            self.mode = "blocked"
            return []

        self.mode = "passthrough"
        released = "".join(self.prefix_parts)
        self.prefix_parts.clear()
        return [released]

    @property
    def raw(self) -> str:
        return "".join(self.raw_parts)

    def finish_passthrough(self) -> list[str]:
        """Release an undecided normal prefix; blocked data stays private."""
        if self.mode == "blocked":
            return []
        if self.mode == "deciding":
            preview = "".join(self.prefix_parts).lstrip().lower()
            markers = ("<thinking>", "<think>")
            if preview and any(marker.startswith(preview) for marker in markers):
                self.mode = "blocked"
                return []
            self.mode = "passthrough"
            released = "".join(self.prefix_parts)
            self.prefix_parts.clear()
            return [released] if released else []
        return []


__all__ = [
    "NormalizedReasoning",
    "ThinkingTagStreamGuard",
    "normalize_reasoning_channels",
]
