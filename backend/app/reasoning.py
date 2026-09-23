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

# Provider chat templates are not part of the assistant's user-facing
# contract.  In particular, OpenAI-compatible endpoints have occasionally
# returned the literal DeepSeek/DSML tool-call envelope in ``content`` (for
# example ``<｜DSML｜ invoke name=\"bash\">``) when a tool-capable model is
# used without tool execution enabled.  Treat these markers as an invalid
# provider response rather than allowing them to reach the transcript.
#
# Keep this deliberately narrow: ordinary angle-bracket text (including
# comparisons and HTML-like prose) is not a protocol leak.  The validator
# uses the same predicate for non-streaming completions.
_INTERNAL_PROTOCOL_PATTERN = re.compile(
    r"(?:"
    r"<\s*(?:[|｜]\s*)?dsml\s*(?:[|｜]\s*)?(?:calls?|invoke|function(?:_calls?)?|tool(?:_calls?)?)\b[^>]*>"
    r"|<\s*(?:tool_calls?|function_calls?|invoke)\b[^>]*>"
    r"|<\s*[|｜]\s*(?:tool_calls?|function_calls?|assistant\s+to=|python_tag)\b[^>]*[|｜]\s*>"
    r"|^\s*(?:```(?:json)?\s*)?\{\s*[\"']tool_calls?[\"']\s*:"
    r")",
    flags=re.IGNORECASE | re.DOTALL,
)

# Prefixes used by the streaming guard.  A suffix which is a prefix of one of
# these markers is held for one more chunk, so a marker split across provider
# deltas cannot leak one character at a time.
_INTERNAL_PROTOCOL_PREFIXES = (
    "<｜dsml｜",
    "<|dsml|",
    "<dsml",
    "<tool_call",
    "<function_call",
    "<invoke",
    "<|tool_call",
    "<|function_call",
    "<|assistant to=",
    "<|python_tag",
    '{"tool_call',
    "{'tool_call",
)


def contains_internal_protocol(text: str | None) -> bool:
    """Return whether *text* contains a provider/tool protocol envelope.

    This is a transport-integrity check, not a semantic quality check.  It is
    intentionally exported so ``answer_validator`` and streaming code cannot
    drift into using subtly different leak predicates.
    """

    if not text:
        return False
    return bool(_INTERNAL_PROTOCOL_PATTERN.search(text))


def _protocol_prefix_suffix(text: str) -> str:
    """Return the longest suffix that could begin a protocol marker."""

    lowered = text.casefold()
    # DSML has optional whitespace around its full-width separators (and the
    # separator itself is often split into a separate provider delta).  Once a
    # tail contains the distinctive ``<...dsml``/tool-call stem but no closing
    # ``>``, retain the whole tail instead of releasing it character by
    # character.
    start = lowered.rfind("<")
    if start >= 0:
        tail = lowered[start:]
        if ">" not in tail and (
            "dsml" in tail
            or "tool_call" in tail
            or "function_call" in tail
            or "assistant to=" in tail
            or "python_tag" in tail
        ):
            return text[start:]
    for length in range(min(len(text), 80), 0, -1):
        suffix = lowered[-length:]
        if any(prefix.startswith(suffix) for prefix in _INTERNAL_PROTOCOL_PREFIXES):
            return text[-length:]
    return ""


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
    """Hold streams that may contain provider-only protocol markers.

    Ordinary prose is released after a handful of prefix characters, so the
    normal fast first-token experience remains.  If the prefix is
    ``<thinking>``/``<think>``, or a DSML/tool-call envelope appears, the
    malformed payload is held until the final channel normaliser can separate
    it safely.  DSML is never a user-facing answer: unlike a thinking block it
    is not repaired into visible text and therefore results in an empty/failed
    completion that the answer validator can handle.
    """

    raw_parts: list[str]
    prefix_parts: list[str]
    mode: str = "deciding"  # deciding | passthrough | blocked
    invalid_protocol: bool = False

    @classmethod
    def create(cls) -> "ThinkingTagStreamGuard":
        return cls(raw_parts=[], prefix_parts=[])

    def push(self, delta: str) -> list[str]:
        self.raw_parts.append(delta)
        if self.mode == "passthrough":
            # A provider can emit a normal-looking prefix before beginning a
            # tool envelope.  Check every subsequent chunk as well; otherwise
            # the literal ``invoke`` payload would be sent as a normal answer.
            combined = "".join(self.prefix_parts) + delta
            if contains_internal_protocol(combined):
                self.mode = "blocked"
                self.invalid_protocol = True
                self.prefix_parts.clear()
                return []

            suffix = _protocol_prefix_suffix(combined)
            if suffix:
                self.prefix_parts = [suffix]
                return [combined[:-len(suffix)]] if len(combined) > len(suffix) else []
            self.prefix_parts.clear()
            return [combined]
        if self.mode == "blocked":
            return []

        self.prefix_parts.append(delta)
        preview = "".join(self.prefix_parts).lstrip()
        if not preview:
            return []

        if contains_internal_protocol(preview):
            self.mode = "blocked"
            self.invalid_protocol = True
            self.prefix_parts.clear()
            return []

        markers = ("<thinking>", "<think>")
        lowered = preview.casefold()
        if any(marker.startswith(lowered) for marker in markers):
            return []
        if lowered.startswith(markers):
            self.mode = "blocked"
            return []

        # Keep a possible protocol prefix across delta boundaries.  This is
        # the same fail-closed behaviour as the thinking-tag path, but applies
        # after ordinary prose has already been released too.
        suffix = _protocol_prefix_suffix(preview)
        if suffix and preview.endswith(suffix):
            # Only hold a suffix; release any safe text before it.
            safe = preview[:-len(suffix)]
            self.prefix_parts = [suffix]
            if safe:
                self.mode = "passthrough"
                return [safe]
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
            preview = "".join(self.prefix_parts).lstrip()
            markers = ("<thinking>", "<think>")
            lowered = preview.casefold()
            if preview and any(marker.startswith(lowered) for marker in markers):
                self.mode = "blocked"
                return []
            if contains_internal_protocol(preview):
                self.mode = "blocked"
                self.invalid_protocol = True
                return []
            self.mode = "passthrough"
            released = "".join(self.prefix_parts)
            self.prefix_parts.clear()
            return [released] if released else []
        return []


__all__ = [
    "contains_internal_protocol",
    "NormalizedReasoning",
    "ThinkingTagStreamGuard",
    "normalize_reasoning_channels",
]
