"""Bounded retries before a main reply stream has been accepted."""
import asyncio
import logging
import random
from time import monotonic

import openai
from .deadline import timeout

logger = logging.getLogger(__name__)
MAIN_RETRY_LIMIT = 5
MAIN_ATTEMPT_SECONDS = 180.0
MAIN_TOTAL_SECONDS = (MAIN_RETRY_LIMIT + 1) * MAIN_ATTEMPT_SECONDS + MAIN_RETRY_LIMIT * 5


def retryable_overload(exc):
    if not isinstance(exc, openai.APIStatusError):
        return False
    body = str(getattr(exc, "body", "") or exc.message).casefold()
    if any(term in body for term in ("quota", "balance", "credit", "billing", "额度", "余额")):
        return False
    return exc.status_code in (429, 503)


async def create_with_overload_retry(provider, **kwargs):
    # Only retry create(): never replay reasoning/content or tools from an
    # accepted stream. Auxiliary models retain their own existing policy.
    client = provider._main_client()
    if not provider.main_reply_enabled:
        return await client.chat.completions.create(**kwargs)
    notify = getattr(provider, "on_main_retry", None)
    try:
        for attempt in range(MAIN_RETRY_LIMIT + 1):
            provider.main_attempt_started_at = monotonic()
            try:
                async with timeout(MAIN_ATTEMPT_SECONDS):
                    return await client.chat.completions.create(**kwargs)
            except (openai.APIStatusError, openai.APIConnectionError, TimeoutError, asyncio.TimeoutError) as exc:
                transient = retryable_overload(exc) or isinstance(exc, (openai.APIConnectionError, TimeoutError, asyncio.TimeoutError))
                if attempt == MAIN_RETRY_LIMIT or not transient:
                    raise
                delay = random.uniform(1.0, 2.0)
                if isinstance(exc, openai.APIStatusError):
                    try:
                        delay = max(delay, min(5.0, float(exc.response.headers.get("retry-after", "0"))))
                    except (TypeError, ValueError):
                        pass
                record = {"attempt": attempt + 1, "status": getattr(exc, "status_code", None),
                          "reason": type(exc).__name__, "request_id": getattr(exc, "request_id", None),
                          "delay_seconds": round(delay, 3)}
                provider.overload_retries.append(record)
                if notify:
                    notify({"retrying": True, "attempt": attempt + 1, "max_retries": MAIN_RETRY_LIMIT})
                logger.warning("Main reply retry: model=%s attempt=%s status=%s reason=%s", provider.model,
                               attempt + 1, record["status"], record["reason"])
                await asyncio.sleep(delay)
    finally:
        if notify and provider.overload_retries:
            notify({"retrying": False, "attempt": len(provider.overload_retries), "max_retries": MAIN_RETRY_LIMIT})
