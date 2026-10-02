"""Request-scoped compression settings over existing provider transports."""

from copy import copy

from .generation_policy import is_ark_kimi
from .providers.base import ProviderError
from .providers.deadline import timeout


class HistoryCompressor:
    def __init__(self, default_provider, settings):
        self.default_provider = default_provider
        self.settings = settings
        self.name = settings.main_history_compression_provider or default_provider.name
        self._provider = None

    async def route_detailed(self, *, system, user, max_tokens=None):
        # Resolve lazily: a missing optional compression provider must not
        # prevent ordinary requests that do not need compression.
        if self._provider is None:
            from .providers import get_provider
            original = (get_provider(self.name)
                        if self.settings.main_history_compression_provider
                        else self.default_provider)
            scoped = copy(original)
            scoped.thinking_override = None
            if hasattr(original, "_settings"):
                model = self.settings.main_history_compression_model or original.model
                scoped.model = model
                scoped._settings = original._settings.model_copy(update={
                    f"{self.name}_router_model": model,
                    "router_request_timeout_seconds": self.settings.main_history_compression_timeout_seconds,
                    "background_model_timeout_seconds": self.settings.main_history_compression_timeout_seconds,
                })
                scoped._client = original._client.with_options(
                    timeout=self.settings.main_history_compression_timeout_seconds,
                    max_retries=0,
                )
            self._provider = scoped

        provider = self._provider
        effort = self.settings.main_history_compression_effort
        ark_kimi = (provider.name == "deepseek" and hasattr(provider, "_settings") and
                    is_ark_kimi(provider._settings, provider.name, model=provider.model))
        # The explicit effort contract is verified only on the K3 transport.
        # Other providers retain their own routing parameter conventions.
        if effort not in {"low", "provider_default"} and not ark_kimi:
            raise ProviderError("history_compression_effort_requires_ark_kimi")
        async with timeout(self.settings.main_history_compression_timeout_seconds):
            return await provider.route_detailed(
                system=system, user=user,
                max_tokens=self.settings.main_history_compression_max_tokens,
                include_reasoning=ark_kimi,
                reasoning_effort=effort if ark_kimi else None,
            )
