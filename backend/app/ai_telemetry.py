"""Normalised, best-effort telemetry for every LLM execution stage."""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import get_settings
from .models import AIExecutionEvent, Conversation

logger = logging.getLogger(__name__)


def _pricing() -> tuple[dict[str, dict[str, float]], str]:
    raw = get_settings().llm_price_per_million_json or "{}"
    version = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}, version
    return (payload if isinstance(payload, dict) else {}), version


def estimate_cost(model: str | None, usage: dict[str, int] | None) -> tuple[Decimal | None, str]:
    prices, version = _pricing()
    rate = prices.get(model or "")
    if not isinstance(rate, dict) or not usage:
        return None, version
    total = Decimal("0")
    for usage_key, price_key in (
        ("input_tokens", "input"),
        ("output_tokens", "output"),
        ("reasoning_tokens", "reasoning"),
    ):
        tokens = int(usage.get(usage_key, 0) or 0)
        price = rate.get(price_key)
        if price is not None:
            total += Decimal(tokens) * Decimal(str(price)) / Decimal(1_000_000)
    return total, version


async def add_ai_event(
    db: AsyncSession,
    *,
    stage: str,
    session_id: str | None,
    subject_id: str | None,
    provider: str | None,
    model_name: str | None,
    duration_ms: int | None = None,
    usage: dict[str, int] | None = None,
    assistant_message_id: int | None = None,
    request_id: str | None = None,
    finish_reason: str | None = None,
    error_code: str | None = None,
    prompt_version: str | None = None,
    event_metadata: dict[str, Any] | None = None,
) -> AIExecutionEvent:
    conversation_id = None
    if session_id:
        conversation_id = (
            await db.execute(
                select(Conversation.id).where(Conversation.session_id == session_id)
            )
        ).scalar_one_or_none()
    usage = usage or {}
    cost, pricing_version = estimate_cost(model_name, usage)
    event = AIExecutionEvent(
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        subject_id=subject_id,
        session_id=session_id,
        stage=stage,
        provider=provider,
        model_name=model_name,
        duration_ms=duration_ms,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        reasoning_tokens=usage.get("reasoning_tokens"),
        provider_request_id=request_id,
        finish_reason=finish_reason,
        error_code=error_code,
        prompt_version=prompt_version,
        estimated_cost_usd=cost,
        pricing_version=pricing_version,
        event_metadata=event_metadata,
    )
    db.add(event)
    return event


async def save_ai_event(
    sessionmaker: async_sessionmaker[AsyncSession], **kwargs: Any
) -> None:
    """Detached-stage convenience wrapper; telemetry never breaks chat."""
    try:
        async with sessionmaker() as db:
            await add_ai_event(db, **kwargs)
            await db.commit()
    except Exception:  # noqa: BLE001 — observability must never break the work observed
        logger.exception("failed to persist AI telemetry stage=%s", kwargs.get("stage"))
