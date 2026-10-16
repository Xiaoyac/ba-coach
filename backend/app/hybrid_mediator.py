"""Bounded mediation for admin hybrid retrieval, isolated from chat thinking."""
from time import perf_counter
from .knowledge_mediator import mediate_knowledge, MEDIATOR_TIMEOUT_CAP_SECONDS
from .providers.deepseek import DeepSeekProvider


def build_provider(settings):
    deadline = min(settings.knowledge_mediator_timeout_seconds, MEDIATOR_TIMEOUT_CAP_SECONDS)
    scoped = settings.model_copy(update={
        "deepseek_model": settings.knowledge_hybrid_mediator_model,
        "deepseek_router_model": settings.knowledge_hybrid_mediator_model,
        "provider_max_retries": 0,
        "provider_request_timeout_seconds": deadline,
        "router_request_timeout_seconds": deadline,
        "background_model_timeout_seconds": deadline,
    })
    # K3's provider adapter maps disabled to low; models supporting disabled
    # receive thinking=disabled. Never inherit the main/router override.
    return DeepSeekProvider(scoped).with_thinking(False)


async def mediate_hybrid_knowledge(*, settings, **kwargs):
    # The caller has already checked the independent per-turn node switch.
    # The historical architecture boolean and server legacy flag are unrelated.
    scoped = settings.model_copy(update={
        "knowledge_mediator_enabled": True,
        "knowledge_mediator_include_reasoning": False,
        "knowledge_mediator_reasoning_effort": "low",
    })
    provider = None
    started = perf_counter()
    if kwargs.get("debug_output") is not None:
        kwargs["debug_output"].clear()
    try:
        # No client/key needed for empty retrieval or policy-only skips.
        from .knowledge_policy import declined_recording_result
        if kwargs["knowledge"] and declined_recording_result(
                kwargs["state"], kwargs["module"], kwargs["knowledge"]) is None:
            provider = build_provider(scoped)
        result = await mediate_knowledge(settings=scoped, provider=provider,
            capture_response=True, **kwargs)
        from .generation_policy import is_ark_kimi
        result[2].update(provider="deepseek", policy="hybrid_low_latency",
                         requested_model=scoped.knowledge_hybrid_mediator_model,
                         effective_thinking="low" if is_ark_kimi(scoped, "deepseek",
                             model=scoped.knowledge_hybrid_mediator_model) else "disabled")
        return result
    except Exception:
        # Configuration/client construction must not crash the foreground reply
        # or fall back to unreviewed raw chunks. Never emit credential-bearing errors.
        return [], "", {"status": "fallback", "reason": "invalid_output_or_provider_error",
            "duration_ms": round((perf_counter() - started) * 1000),
            "provider": "deepseek", "policy": "hybrid_low_latency",
            "requested_model": scoped.knowledge_hybrid_mediator_model,
            "raw_chunk_count": len(kwargs["knowledge"]), "approved_chunk_count": 0,
            "withheld_on_error": True}
    finally:
        if provider is not None:
            await provider._client.close()
