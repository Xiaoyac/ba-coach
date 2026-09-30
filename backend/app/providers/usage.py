"""Normalise OpenAI-compatible usage without inventing cache measurements."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


CACHE_USAGE_KEYS = ("cache_read_input_tokens", "cache_creation_input_tokens")


def _field(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _count(value: Any) -> int | None:
    # Missing/invalid counts are unknown, including false and fractional values.
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _first_count(*values: Any) -> int | None:
    for value in values:
        count = _count(value)
        if count is not None:
            return count
    return None


def cache_usage(usage: Mapping[str, Any]) -> dict[str, int]:
    """Select measured cache counts for telemetry; absent is not zero."""
    return {key: count for key in CACHE_USAGE_KEYS
            if (count := _count(usage.get(key))) is not None}


def openai_usage(usage: Any) -> dict[str, int]:
    """Accept SDK usage objects or JSON dictionaries on every call path.

    Keep the existing three token counters. Cache read/write measurements are
    additive diagnostics and appear only when the upstream API supplies them.
    A cache miss does not prove that tokens were written to a cache.
    """
    prompt_details = _field(usage, "prompt_tokens_details")
    completion_details = _field(usage, "completion_tokens_details")
    result = {
        "input_tokens": _count(_field(usage, "prompt_tokens")) or 0,
        "output_tokens": _count(_field(usage, "completion_tokens")) or 0,
        "reasoning_tokens": _count(_field(completion_details, "reasoning_tokens")) or 0,
    }
    read = _first_count(
        _field(prompt_details, "cached_tokens"),
        _field(usage, "cache_read_input_tokens"),
        _field(usage, "prompt_cache_hit_tokens"),
    )
    write = _first_count(
        _field(prompt_details, "cache_creation_input_tokens"),
        _field(prompt_details, "cache_creation_tokens"),
        _field(usage, "cache_creation_input_tokens"),
    )
    if read is not None:
        result["cache_read_input_tokens"] = read
    if write is not None:
        result["cache_creation_input_tokens"] = write
    return result
