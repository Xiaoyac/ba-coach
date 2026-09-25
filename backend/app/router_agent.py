"""Select this turn's module before reply generation.

The router proposes a module from the current user message, prior dialogue and
committed state. It never sees an ungenerated current assistant answer. Business
confirmation and persistence remain the responsibility of the commit service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field, replace
from time import perf_counter
from pathlib import Path

from .providers.base import LLMProvider
from .reasoning import normalize_reasoning_channels
from .workflow_state import normalise_completed_steps

logger = logging.getLogger(__name__)

ROUTER_RUNTIME_CONTRACT = """# 路由接口边界
在生成本轮回复之前判断；输入只有当前用户发言、此前对话与已提交状态。
不要生成用户可见文案，不根据字段空缺创造追问任务，不把模型建议当作已保存状态。
离开 M1 后不返回 M1；后续模块内的 BA 理解问题由当前模块澄清，暂停具体目标推进。
输出 JSON {"target_module":"1","knowledge_task":"general"}。兼容历史单个模块编号；completed_steps 不是必填字段。
knowledge_task只根据当前用户问题选择知识所需事实范围，不根据字段缺失创造问题或任务。
正式模块迁移需要后台提交成功；失败保留实际已提交模块。
"""
ROUTER_AGENT_PROMPT = (Path(__file__).with_name("prompt_defaults") / "router.md").read_text(encoding="utf-8")

# The prompt's own vocabulary — "current_module=1", "target_module" — so the
# router's input/output stays in the same short digit form it already reasons
# in; the graph deals in "module_1".."module_4" everywhere else, so these are
# the only two places that translate between the two.
_SHORT_TO_FULL: dict[str, str] = {
    "1": "module_1",
    "2": "module_2",
    "3": "module_3",
    "4": "module_4",
}
_FULL_TO_SHORT: dict[str, str] = {v: k for k, v in _SHORT_TO_FULL.items()}

# The only transitions the business rules actually describe (plus staying
# put, always valid). Anything else the model proposes — skipping a step,
# jumping backward, inventing a module — is not a graded judgement call, it's
# a structural violation, so it is rejected outright rather than "mostly
# trusted."
_VALID_MOVES: dict[str, set[str]] = {
    "1": {"1", "2"},
    "2": {"2", "3"},
    "3": {"2", "3", "4"},
    "4": {"4", "2", "3"},
}

_PA_CARD_RE = re.compile(r"当前(?:核心\s*)?PA\s*目标")


@dataclass(frozen=True)
class RouterDecision:
    """Clamped module decision plus the provider's separate thought trace."""

    target_module: str
    reasoning_content: str
    model: str
    completed_steps: list[str]
    usage: dict[str, int]
    finish_reason: str | None = None
    request_id: str | None = None
    error_code: str | None = None
    revoked_steps: list[str] = field(default_factory=list)
    revocation_evidence: str | None = None
    json_recovery: dict = field(default_factory=dict)
    knowledge_task: str = "general"


def format_routing_reasoning(decision, current, applied_target=None, *, diagnostics=None):
    """Never replace native reasoning with a database-veto notice."""
    applied = applied_target or decision.target_module
    result = (f"模块判断结果：维持 {applied}，本轮不跳转。" if applied == current
              else f"模块判断结果：{current} → {applied}。")
    notices = []
    if decision.error_code:
        notices.append(f"路由判断未成功（{decision.error_code}），模型没有提供可用决定；最终阶段由服务器契约核验，不将失败标记为正常判断。")
    if decision.json_recovery:
        notices.append("路由首次输出达到长度上限；已进行一次限时的结构化判断恢复，状态："
                       + decision.json_recovery["status"] + "。该恢复不额外生成深度思考。")
    if applied != decision.target_module:
        notices.append(f"Router建议：{decision.target_module}；后台核验后的实际阶段：{applied}。"
                       "模型建议不是数据库已执行的切换；下方保留原始模型思考，不能把它当作最终状态。")
    if diagnostics and diagnostics.get("block_reasons"):
        notices.append("本轮未跳转原因：" + "；".join(diagnostics["block_reasons"]) + "。")
    elif diagnostics and diagnostics.get("policy") == "router_evidence_reconciled":
        notices.append("已采纳 Router 的推进建议，并从真实对话中补正理解／同意证据。")
    goal_creation = (diagnostics or {}).get("goal_creation") if diagnostics else None
    if goal_creation and goal_creation.get("status") == "blocked":
        notices.append("目标创建未执行：" + goal_creation.get("message", "当前目标证据尚未通过核验")
                       + f"（{goal_creation.get('reason_code', 'unknown')}）。")
    elif goal_creation and goal_creation.get("status") == "created":
        notices.append("已根据用户明确选择和当前轮次证据创建目标草稿。")
    thought = decision.reasoning_content.strip() or (
        "路由模型未返回独立 reasoning_content；有无思考文本不等于判断是否成功，请结合上述状态。")
    return "\n\n".join([result, *notices, thought])


def extract_pa_card(text: str) -> str | None:
    """Pull the fixed-format PA goal card out of a reply, if present.

    Used both to gate module 3/4 entry (the rule: no card on record, no
    entry) and to keep the card itself in memory so modules 3 and 4 can
    reference what it actually says.
    """
    if not text:
        return None
    marker = _PA_CARD_RE.search(text)
    return text[marker.start():].strip() if marker else None


def _clamp(
    current: str,
    proposed: str,
    *,
    has_pa_card: bool,
    completed_steps: list[str],
) -> str:
    """Re-apply the hard structural rules in code.

    The prompt states these same rules, but an LLM is a probabilistic
    reasoner asked to follow them, not a state machine that structurally
    cannot violate them. This is the deterministic backstop: a model that
    gets the nuanced calls right nearly all the time should still never be
    able to skip a step or unlock module 3/4 without a goal card just
    because it misread one turn.
    """
    if proposed not in _VALID_MOVES.get(current, {current}):
        return current
    if proposed in ("3", "4") and not has_pa_card:
        return current
    return proposed


def _parse_target_module(raw: str) -> str | None:
    text = raw.strip()
    if text.startswith("```"):
        # Models occasionally fence JSON despite being told not to.
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    # Some reasoning models honour the *meaning* of the schema but return the
    # bare value (``1`` / ``module_1`` / ``模块1``). Treat those as the same
    # unambiguous decision instead of holding the workflow in place forever.
    compact = text.lower().replace(" ", "")
    bare = re.fullmatch(r"(?:module_|模块)?([1-4])", compact)
    if bare:
        return bare.group(1)

    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    target = payload.get("target_module")
    if target is None:
        return None
    compact_target = str(target).strip().lower().replace(" ", "")
    matched = re.fullmatch(r"(?:module_|模块)?([1-4])", compact_target)
    return matched.group(1) if matched else compact_target


def _parse_completed_steps(raw: str, module: str) -> list[str]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        payload = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    return normalise_completed_steps(module, payload.get("completed_steps"))


async def decide_target_module(
    provider: LLMProvider,
    *,
    current_module: str,
    user_input: str,
    ai_output: str = "",
    has_pa_card: bool,
    conversation_context: str = "",
    system_prompt: str | None = None,
    completed_steps: list[str] | None = None,
) -> str:
    """Propose which module should handle the current incoming user message.

    `current_module` and the return value are both full "module_N" ids —
    translation to/from the prompt's short digit form happens internally.
    Never raises; any failure (bad JSON, an unknown value, an empty router
    response) holds the conversation on `current_module`.
    """
    decision = await decide_target_module_with_reasoning(
        provider,
        current_module=current_module,
        user_input=user_input,
        ai_output=ai_output,
        has_pa_card=has_pa_card,
        conversation_context=conversation_context,
        system_prompt=system_prompt,
        completed_steps=completed_steps,
    )
    return decision.target_module


async def decide_target_module_with_reasoning(
    provider: LLMProvider,
    *,
    current_module: str,
    user_input: str,
    ai_output: str = "",
    has_pa_card: bool,
    conversation_context: str = "",
    system_prompt: str | None = None,
    max_tokens: int | None = None,
    completed_steps: list[str] | None = None,
    recovery_timeout_seconds: float = 6.0,
    business_state: dict | None = None,
) -> RouterDecision:
    """Return this turn's proposed module together with its thought trace.

    ``ai_output`` is retained only for old callers and is deliberately ignored.

    The final JSON remains the only text parsed as a decision. Native
    ``reasoning_content`` is carried separately for the UI and can never
    bypass the deterministic transition clamp.
    """
    current_short = _FULL_TO_SHORT.get(current_module, "1")
    effective_system = (system_prompt or ROUTER_AGENT_PROMPT) + "\n\n" + ROUTER_RUNTIME_CONTRACT
    from .knowledge_context import KNOWLEDGE_TASKS
    effective_system += "\nknowledge_task 可用值：" + ", ".join(KNOWLEDGE_TASKS)
    history_block = conversation_context.strip() or "（本 Session 无更早对话）"
    user_message = (
        f"current_module：{current_short}\n"
        f"记忆库中是否存在本轮PA目标卡片：{'是' if has_pa_card else '否'}\n\n"
        f"本 Session 对话记录（按时间顺序，必须综合判断）：\n{history_block}\n\n"
        f"用户本轮输入：\n{user_input}\n\n"
        f"\n已提交的紧凑业务状态（事实参考，不是追问清单）：\n"
        f"{json.dumps(business_state or {}, ensure_ascii=False)}\n"
    )

    try:
        completion = await provider.route_with_reasoning(
            system=effective_system,
            user=user_message,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("pre-reply router failed; preserving current module: %s", type(exc).__name__)
        return RouterDecision(target_module=current_module, reasoning_content="",
            model=getattr(provider, "model", ""), completed_steps=[], usage={},
            error_code="router_timeout" if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else "router_provider_error")
    normalized = normalize_reasoning_channels(
        completion.text, completion.reasoning_content
    )
    raw = normalized.reply
    recovery = {}
    if completion.finish_reason in ("length", "max_tokens"):
        # A thinking budget exhausted before JSON is not a successful route.
        # Recover once with the same full input, no native reasoning and a
        # small output budget; never allow an unbounded retry loop.
        started = perf_counter()
        recovery = {"status": "failed", "original_finish_reason": completion.finish_reason,
                    "original_request_id": completion.request_id}
        original = completion
        raw = ""
        try:
            recovered = await asyncio.wait_for(provider.route_detailed(
                system=effective_system + "\n仅返回完整判断JSON，不要解释，不要增加输出字段。",
                user=user_message, max_tokens=512, include_reasoning=False), timeout=recovery_timeout_seconds)
            recovery["request_id"] = recovered.request_id
            totals = {key: (original.usage or {}).get(key, 0) + (recovered.usage or {}).get(key, 0)
                      for key in set(original.usage or {}) | set(recovered.usage or {})}
            completion = replace(original, usage=totals)
            candidate = normalize_reasoning_channels(recovered.text, recovered.reasoning_content).reply
            try:
                payload = json.loads(candidate.strip().removeprefix("```json").removesuffix("```").strip())
            except (ValueError, TypeError):
                payload = None
            valid_shape = (_parse_target_module(candidate) in _SHORT_TO_FULL
                           and (not isinstance(payload, dict) or "completed_steps" not in payload
                                or (isinstance(payload["completed_steps"], list)
                                    and all(isinstance(step, str) for step in payload["completed_steps"]))))
            if (recovered.finish_reason not in ("length", "max_tokens") and valid_shape
                    and _parse_target_module(candidate) in _SHORT_TO_FULL):
                raw = candidate
                completion = replace(completion, text=candidate, finish_reason=recovered.finish_reason)
                recovery["status"] = "recovered"
        except asyncio.TimeoutError:
            recovery["status"] = "timeout"
        except Exception:  # recovery failure still holds position, not the HTTP reply
            recovery["status"] = "provider_error"
        recovery["duration_ms"] = round((perf_counter() - started) * 1000)
    if not raw:
        logger.warning("router agent returned nothing; keeping module_%s", current_short)
        return RouterDecision(
            target_module=current_module,
            reasoning_content=normalized.reasoning,
            model=completion.model,
            completed_steps=[],
            usage=completion.usage,
            finish_reason=completion.finish_reason,
            request_id=completion.request_id,
            error_code="router_json_recovery_failed" if recovery else "empty_completion",
            json_recovery=recovery,
        )

    proposed = _parse_target_module(raw)
    if proposed not in _SHORT_TO_FULL:
        logger.warning(
            "router agent returned unparseable target_module %r; keeping module_%s",
            raw,
            current_short,
        )
        return RouterDecision(
            target_module=current_module,
            reasoning_content=normalized.reasoning,
            model=completion.model,
            completed_steps=[],
            usage=completion.usage,
            finish_reason=completion.finish_reason,
            request_id=completion.request_id,
            error_code="invalid_router_json",
            json_recovery=recovery,
        )

    revoked_steps, revocation_evidence = [], None
    try:
        correction = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        quote = correction.get("revocation_evidence")
        if isinstance(quote, str) and quote.strip() and quote in user_input:
            revoked_steps = normalise_completed_steps(current_module, correction.get("revoked_steps"))
            revocation_evidence = quote if revoked_steps else None
    except (ValueError, AttributeError, TypeError):
        pass
    completed_steps = normalise_completed_steps(
        current_module,
        (completed_steps or []) + _parse_completed_steps(raw, current_module),
    )
    completed_steps = [s for s in completed_steps if s not in revoked_steps]
    resolved = _clamp(
        current_short,
        current_short if revoked_steps else proposed,
        has_pa_card=has_pa_card,
        completed_steps=completed_steps,
    )
    from .knowledge_context import valid_knowledge_task
    knowledge_task = "general"
    try:
        payload = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        candidate = payload.get("knowledge_task") if isinstance(payload, dict) else None
        if isinstance(candidate, str) and valid_knowledge_task(candidate, _SHORT_TO_FULL[resolved]):
            knowledge_task = candidate
    except (ValueError, TypeError):
        pass
    return RouterDecision(
        target_module=_SHORT_TO_FULL[resolved],
        knowledge_task=knowledge_task,
        revoked_steps=revoked_steps,
        revocation_evidence=revocation_evidence,
        reasoning_content=normalized.reasoning,
        model=completion.model,
        completed_steps=completed_steps,
        usage=completion.usage,
        finish_reason=completion.finish_reason,
        request_id=completion.request_id,
        error_code=None,
        json_recovery=recovery,
    )


__all__ = [
    "ROUTER_AGENT_PROMPT",
    "ROUTER_RUNTIME_CONTRACT",
    "RouterDecision",
    "decide_target_module",
    "decide_target_module_with_reasoning",
    "extract_pa_card",
    "format_routing_reasoning",
]
