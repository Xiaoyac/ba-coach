"""Bounded recovery of a reasoning-only, length-exhausted main reply.

Only produces a candidate: graph normalization/validation still run afterwards.
No raw retrieved knowledge, new user message or synthetic workflow transition.
"""
import asyncio
from time import perf_counter

from .providers.base import ProviderError

GENERATION_INTERRUPTED_REPLY = (
    "抱歉，这次回复生成中断了，不是你说得不清楚。"
    "请稍后重试，不需要为此补充或改写刚才的内容。"
)
RECOVERY_MAX_SECONDS = 8.0


async def recover_empty_reply(*, provider, system, messages, elapsed_seconds,
                              total_timeout_seconds, finish_reason, usage):
    diagnostic = {"attempted": False, "original_finish_reason": finish_reason,
                  "original_usage": dict(usage), "status": "not_eligible"}
    # Called only when the normalized visible reply is empty. Refusals, normal
    # stops and errors do not get silently re-requested as if they were truncation.
    if finish_reason != "length":
        return None, diagnostic
    recovery = getattr(provider, "complete_without_reasoning", None)
    if not callable(recovery):
        diagnostic["status"] = "unsupported_provider"
        return None, diagnostic
    budget = min(RECOVERY_MAX_SECONDS, max(0, total_timeout_seconds - elapsed_seconds))
    if budget <= 0:
        diagnostic["status"] = "budget_exhausted"
        return None, diagnostic
    diagnostic.update(attempted=True, budget_seconds=round(budget, 3))
    started = perf_counter()
    try:
        result = await asyncio.wait_for(recovery(system=system, messages=messages), timeout=budget)
        diagnostic.update(finish_reason=result.finish_reason, usage=result.usage,
                          request_id=result.request_id)
        if not result.text.strip() or result.finish_reason != "stop":
            diagnostic["status"] = "invalid_completion"
            return None, diagnostic
        diagnostic["status"] = "recovered"
        return result, diagnostic
    except (TimeoutError, asyncio.TimeoutError):
        diagnostic["status"] = "timeout"
        return None, diagnostic
    except ProviderError:
        diagnostic["status"] = "provider_error"
        return None, diagnostic
    except Exception:
        # Malformed compatible-provider payloads are also an upstream failure;
        # recovery must not break the original turn. CancelledError is a
        # BaseException on supported Python versions and still propagates.
        diagnostic["status"] = "invalid_provider_response"
        return None, diagnostic
    finally:
        # Cancellation propagates; a user stop never starts another attempt.
        diagnostic["duration_ms"] = round((perf_counter() - started) * 1000)
