"""Dedicated model for module memories, independent of chat and Router models."""
from contextlib import suppress
from .execution_status import track_call

MODEL = 'deepseek-v4.1-flash'


async def summarize_module(provider, *, system, user, max_tokens):
    owned = None
    try:
        # Custom/test providers retain their transport; configured production
        # providers use the existing DeepSeek-compatible channel explicitly.
        if hasattr(provider, '_settings'):
            from .providers import get_provider
            from .providers.deepseek import DeepSeekProvider
            source = provider if provider.name == 'deepseek' else get_provider('deepseek')
            settings = source._settings.model_copy(update={
                'deepseek_model': MODEL, 'deepseek_router_model': MODEL,
            })
            owned = DeepSeekProvider(settings)
            provider = owned
        model = MODEL if owned is not None else getattr(provider, "model", None)
        return await track_call('module_summarizer', provider.route_detailed(
            system=system, user=user, max_tokens=max_tokens), model=model)
    finally:
        if owned is not None:
            with suppress(Exception):
                await owned._client.close()
