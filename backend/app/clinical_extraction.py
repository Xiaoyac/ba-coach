"""LLM extraction of structured clinical fields from a transcript.

Runs on the cheap router model, never on the conversational one: this is a
parsing job with a fixed output shape, not a therapeutic reply, and it is
dispatched from a background task where latency is invisible but cost is not.

The prompts are built from `clinical_fields`, so the field list the model is
asked for is literally the same tuple `clinical_store.coerce` validates
against. Output is parsed defensively — a model that returns prose, a fenced
code block, or nothing at all yields `{}` rather than an exception, because
the caller is a detached task with no request left to fail.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .clinical_fields import MODULE_SPECS, RISK_SPECS, Spec, prompt_for
from .providers.base import Completion, LLMProvider

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
你是一个信息抽取器，不是对话助手。你的唯一任务是从对话记录中提取结构化字段。

# 规则
- 只输出一个 JSON 对象，不要任何解释、前后文字或代码块标记。
- 只提取对话中**实际出现过**的信息。没有依据的字段一律给 null，禁止推测、补全或合理化。
- 引用用户表述时尽量保留其原话，不要美化或改写。
- 字段值使用中文，与对话保持一致。

# 需要提取的字段
{fields}

# 输出格式
{{"字段名": 值, ...}}，只包含上面列出的字段名。
"""

_RISK_PROMPT = """\
你是一个风险信号检测器。判断本轮用户发言中是否存在自伤或自杀风险信号。

# 规则
- 只输出一个 JSON 对象，不要任何解释或代码块标记。
- 保持克制：绝大多数发言都不含风险信号，risk_status 应为 0。
  「累」「烦」「没意思」「不想上班」这类普通负面情绪**不是**风险信号。
- 只有当用户明确表达想伤害自己、不想活了、或提到具体计划/方式时，才给 risk_status=1。
- 判断依据只能是用户本轮说的话，不要根据整体氛围推测。

# 需要输出的字段
{fields}

# 输出格式
{{"risk_status": 0}} 或包含全部字段的对象。
"""

# Fenced code blocks are the most common way a model wraps JSON despite being
# told not to; unwrapping is cheaper than a retry.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def _parse_json_object(text: str) -> dict[str, Any]:
    """Best-effort JSON object out of a model response. Never raises."""
    if not text or not text.strip():
        return {}

    candidate = text.strip()
    fenced = _FENCE_RE.match(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        # Fall back to the outermost {...} span — catches a model that added a
        # sentence before or after the object despite the instruction.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                return _as_dict(json.loads(candidate[start : end + 1]))
            except json.JSONDecodeError:
                pass
        return _salvage_truncated(candidate)

    return _as_dict(parsed)


def _as_dict(parsed: Any) -> dict[str, Any]:
    return parsed if isinstance(parsed, dict) else {}


def _salvage_truncated(text: str) -> dict[str, Any]:
    """Recover the complete leading fields of a cut-off JSON object.

    A response that hits the token ceiling ends mid-value, and strict parsing
    throws away everything — including the eight fields that *were* finished.
    This walks back to the last comma at nesting depth 1, closes the object
    there, and parses that.

    Raising `extraction_max_tokens` is the real fix and makes this rare; this
    exists so that when a transcript is unusually long, the record loses its
    tail rather than all of it.
    """
    start = text.find("{")
    if start == -1:
        return {}

    depth = 0
    in_string = False
    escaped = False
    last_safe = -1

    for i, ch in enumerate(text[start:], start):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth == 1:
            # A comma at depth 1 ends one complete top-level pair.
            last_safe = i

    if last_safe == -1:
        return {}

    try:
        return _as_dict(json.loads(text[start:last_safe] + "}"))
    except json.JSONDecodeError:
        return {}


async def _extract(
    provider: LLMProvider,
    *,
    system: str,
    specs: tuple[Spec, ...],
    transcript: str,
    max_tokens: int,
) -> dict[str, Any]:
    raw = await provider.route(
        system=system.format(fields=prompt_for(specs)),
        user=transcript,
        max_tokens=max_tokens,
    )
    return _parse_json_object(raw)


async def _extract_detailed(
    provider: LLMProvider,
    *,
    system: str,
    specs: tuple[Spec, ...],
    transcript: str,
    max_tokens: int,
) -> tuple[dict[str, Any], Completion]:
    rendered = system.format(fields=prompt_for(specs))
    completion = await provider.route_detailed(
        system=rendered, user=transcript, max_tokens=max_tokens
    )
    return _parse_json_object(completion.text), completion


async def extract_module_record(
    provider: LLMProvider,
    *,
    module: str,
    transcript: str,
    max_tokens: int,
) -> dict[str, Any]:
    """Pull one module's fields out of the transcript. `{}` on any failure."""
    specs = MODULE_SPECS.get(module)
    if specs is None:
        return {}
    return await _extract(
        provider,
        system=_EXTRACTION_PROMPT,
        specs=specs,
        transcript=transcript,
        max_tokens=max_tokens,
    )


async def extract_module_record_detailed(
    provider: LLMProvider, *, module: str, transcript: str, max_tokens: int
) -> tuple[dict[str, Any], Completion]:
    specs = MODULE_SPECS.get(module)
    if specs is None:
        return {}, Completion(text="", model=provider.model)
    return await _extract_detailed(
        provider,
        system=_EXTRACTION_PROMPT,
        specs=specs,
        transcript=transcript,
        max_tokens=max_tokens,
    )


async def assess_risk(
    provider: LLMProvider, *, user_message: str, max_tokens: int = 256
) -> dict[str, Any]:
    """Check one user turn for risk signals. `{}` on any failure.

    Given only the user's own message rather than the whole transcript: the
    question is what *this person just said*, and feeding back the coach's
    prior replies (which discuss low mood by design) measurably raises false
    positives.
    """
    if not user_message.strip():
        return {}
    return await _extract(
        provider,
        system=_RISK_PROMPT,
        specs=RISK_SPECS,
        transcript=f"用户本轮发言：{user_message}",
        max_tokens=max_tokens,
    )


async def assess_risk_detailed(
    provider: LLMProvider, *, user_message: str, max_tokens: int = 256
) -> tuple[dict[str, Any], Completion]:
    if not user_message.strip():
        return {}, Completion(text="", model=provider.model)
    return await _extract_detailed(
        provider,
        system=_RISK_PROMPT,
        specs=RISK_SPECS,
        transcript=f"用户本轮发言：{user_message}",
        max_tokens=max_tokens,
    )
