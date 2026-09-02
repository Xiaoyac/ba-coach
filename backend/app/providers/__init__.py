"""LLM provider registry.

Each provider wraps its own *official* SDK behind the common `LLMProvider`
interface in `base.py`:

    claude   -> anthropic.AsyncAnthropic
    deepseek -> openai.AsyncOpenAI (base_url=https://api.deepseek.com)
    doubao   -> openai.AsyncOpenAI (Volcengine Ark compatible endpoint)

Providers are constructed lazily and cached, so an unconfigured provider only
raises when something actually tries to use it.
"""

from __future__ import annotations

from functools import lru_cache

from ..config import get_settings
from .base import LLMProvider, ProviderError
from .claude import ClaudeProvider
from .deepseek import DeepSeekProvider
from .doubao import DoubaoProvider

__all__ = ["LLMProvider", "ProviderError", "get_provider", "configured_providers"]


@lru_cache
def _build(name: str) -> LLMProvider:
    settings = get_settings()
    if name == "claude":
        return ClaudeProvider(settings)
    if name == "deepseek":
        return DeepSeekProvider(settings)
    if name == "doubao":
        return DoubaoProvider(settings)
    raise ProviderError(f"Unknown provider: {name!r}")


def get_provider(name: str | None = None) -> LLMProvider:
    settings = get_settings()
    return _build(name or settings.default_provider)


def configured_providers() -> dict[str, bool]:
    """Which providers have an API key set — used by /health."""
    settings = get_settings()
    return {
        "claude": bool(settings.anthropic_api_key),
        "deepseek": bool(settings.deepseek_api_key),
        "doubao": bool(settings.doubao_api_key and settings.doubao_model),
    }
