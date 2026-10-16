"""A separate non-thinking Flash client for the pre-reply risk screen."""
from contextlib import asynccontextmanager

from .providers.deepseek import DeepSeekProvider


@asynccontextmanager
async def risk_provider(router_provider, settings):
    # Preserve dependency-injected offline providers used by graph tests.
    if not hasattr(router_provider, "_settings"):
        yield router_provider
        return
    scoped = settings.model_copy(update={
        "deepseek_model": settings.risk_gate_model,
        "deepseek_router_model": settings.risk_gate_model,
    })
    provider = DeepSeekProvider(scoped).with_thinking(False)
    try:
        yield provider
    finally:
        await provider._client.close()
