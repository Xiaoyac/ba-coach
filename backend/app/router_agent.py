"""Post-hoc module router.

Distinct from the module selection in `graph.nodes.analyze_intent_node`: that
node just reads back whatever `current_module` this router decided last turn
— it makes no judgement calls of its own. This is where the judgement calls
actually happen, and they happen *after* a turn completes, not before: "has
BA education actually finished," "does a PA goal card exist," "did the user
just report execution feedback" are all questions about what was said this
turn, not something guessable from the incoming message alone the way the
old per-message keyword/LLM classifier tried to.

`decide_target_module` is the only entry point. It never raises — a router
failure holds the conversation on its current module rather than breaking
the turn that already completed successfully.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field, replace
from time import perf_counter

from .providers.base import LLMProvider
from .reasoning import normalize_reasoning_channels
from .workflow_state import normalise_completed_steps, required_steps_complete
from .workflow_contract import step_prompt_contract

logger = logging.getLogger(__name__)

ROUTER_RUNTIME_CONTRACT = """\
# 服务器强制的跨轮判定契约（管理员提示词不可覆盖）
- 必须综合“本 Session 对话记录”的所有可见轮次判断模块完成度，不能只看最后一轮。
- 较早轮次已经完成并获用户确认的步骤仍然有效；最后一轮没有复述不代表进度清零。
- module_1 → module_2 以最新 M1 契约为准，覆盖旧提示中强制抑郁循环/必须个性化的要求。个性化路线需事件四要素、当前关系总结基本认可、缓解方法已知有/无；低披露路线需明确不愿披露或个性化分析，可跳过个人收集及分析，不能编造。
- 两条路线均须完成五项 BA 基础教育、用户基本理解、无未解决核心疑问、用户明确愿意开始目标设定；认可总结不是理解 BA，也不是目标意愿。M1 禁止 PA 介绍、选活动或计划。
- 输出必须同时携带本模块已经完成的结构化步骤键；通常累加，用户明确纠正时按下述撤销接口处理。
- 只输出：{"target_module":"1|2|3|4","completed_steps":["合法步骤键", ...]}。
"""

ROUTER_RUNTIME_CONTRACT += step_prompt_contract()
ROUTER_RUNTIME_CONTRACT += "\nM2 的 completed_steps 表示对话证据是否足够；用户在聊天明确同意完整计划时可报告 pa_card_completed。后台核验真实对话证据后保存推进，不需要网页确认。愿意开始讨论目标不等于选择具体活动，不得把目标面板操作当成完成条件。"
ROUTER_RUNTIME_CONTRACT += "\n修正规则优先于旧的只累加规则：若用户本轮明确否定或更正旧完成证据，输出 revoked_steps（合法步骤键）和 revocation_evidence（本轮用户原话的精确片段）。只撤销受影响步骤；没有明确纠正则 revoked_steps=[]，不能因本轮没提及而撤销。撤销后留在当前模块重新讨论。JSON 允许这两个附加字段。"

# ---------------------------------------------------------------------------
# The business-rule prompt. Its transition invariants remain the specified
# behaviour; the evidence section explicitly requires full-session reasoning
# because module-one completion is distributed across several turns. The
# output block writes down the JSON schema the original rule text referred to.
# ---------------------------------------------------------------------------
ROUTER_AGENT_PROMPT = """\
你是 BA Coach 的对话路由代理。执行规则严格遵守，仅输出指定JSON。

# 模块业务含义（仅用于理解业务，不能直接拿来匹配判定）
模块1（BA心理教育）：建立信任、理解实际行为与状态关系（可低披露）、科普BA基础知识。
模块2（目标设定）：仅当模块1完整结束后才允许进入；PA概念介绍，用户确认意向，生成PA目标卡片。
模块3（建立契约）：必须记忆库存在本轮完整PA目标卡片；介绍记录规则，建立行动契约。
模块4（回顾与分析）：必须记忆库存在本轮完整PA目标卡片；模块3确认后可先进入 waiting_execution 等待态，收到PA执行反馈后才进入实际复盘。

## 判断依据
- 必须综合阅读输入中的“本 Session 对话记录”，不能只根据最后一轮判断；模块一的多个完成条件通常分散在不同轮次。
- 已经在较早轮次完成并得到用户确认的步骤，后续没有重复出现也仍视为已完成；禁止因为最后一轮没有再次复述而把进度清零。
- 判断重点是条件是否已经在完整对话中成立，不要求用户使用提示词中的专业术语或固定句式。

## 跳转规则（只有满足条件，才修改target_module；不满足则target_module = current_module）
1. 跳转至模块二：
- current_module=1；必须满足服务器 M1 三里程碑契约。个性化与明确低披露两种路线都允许，但都需要 BA 教育、基本理解、核心疑问已解决及明确目标设定意愿；不得在 M1 越权开展 PA 介绍、活动建议或具体目标设定；
- current_module=4；复盘流程结束，用户提出调整、更换 PA 目标，并准备开启新一轮行动循环。
2.跳转至模块三：current_module=2，且已经得到完整可落地PA目标卡片，准备开始执行PA目标；
3.跳转至模块四 waiting_execution：current_module=3，且已建立PA目标执行契约。此转移只表示等待执行，不代表已有执行反馈；收到完成、未完成、执行受阻或执行过程描述后，才允许把模块4的 execution_reviewed 步骤标记完成并开始复盘；
4.不符合以上任意跳转条件 → target_module = current_module（若为空，则target_module = 1）；

# 注意事项
- 最先做模块1，1结束后不可回到模块1。整体流程循环为：2 → 3 → 4 → 2；
- 用户偏离话题、情绪抵触、答非所问等情况，维持当前模块不变；
- 区分概念：BA = 行为激活（干预方法论 / 策略体系）；PA = 具体行动任务；BA教育中不需要涵盖PA教育；
- BA教育必须在模块1做完，未完成 BA 基础教育，禁止进入模块2；完成 BA 教育后且用户认可BA理念、愿意开始设定行动目标，方可跳转至模块2；
- 若记忆库中无本轮对应的 PA 目标卡片，禁止进入模块 3、4；PA卡片为固定格式，格式如下：
当前PA目标
• 活动内容：
• 时间：
• 地点：
• 时长：
• 频率：
• 潜在障碍：
• 应对方案：

# 输出格式
只输出如下 JSON，不包含任何其他文字、解释或代码块标记：
{"target_module": "1" | "2" | "3" | "4", "completed_steps": ["本模块已完成的步骤键"]}
"""

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
    "3": {"3", "4"},
    "4": {"4", "2"},
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
    if proposed != current and not required_steps_complete(
        _SHORT_TO_FULL[current], completed_steps
    ):
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
    ai_output: str,
    has_pa_card: bool,
    conversation_context: str = "",
    system_prompt: str | None = None,
    completed_steps: list[str] | None = None,
) -> str:
    """Decide which module should handle the *next* turn.

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
    ai_output: str,
    has_pa_card: bool,
    conversation_context: str = "",
    system_prompt: str | None = None,
    max_tokens: int | None = None,
    completed_steps: list[str] | None = None,
    recovery_timeout_seconds: float = 6.0,
) -> RouterDecision:
    """Return the next module together with the router's thought trace.

    The final JSON remains the only text parsed as a decision. Native
    ``reasoning_content`` is carried separately for the UI and can never
    bypass the deterministic transition clamp.
    """
    current_short = _FULL_TO_SHORT.get(current_module, "1")
    history_block = conversation_context.strip() or "（本 Session 无更早对话）"
    user_message = (
        f"current_module：{current_short}\n"
        f"记忆库中是否存在本轮PA目标卡片：{'是' if has_pa_card else '否'}\n\n"
        f"本 Session 对话记录（按时间顺序，必须综合判断）：\n{history_block}\n\n"
        f"用户本轮输入：\n{user_input}\n\n"
        f"AI 本轮输出：\n{ai_output}\n"
        f"\n服务器已记录的本模块完成步骤："
        f"{json.dumps(normalise_completed_steps(current_module, completed_steps or []), ensure_ascii=False)}\n"
    )

    completion = await provider.route_with_reasoning(
        system=system_prompt or ROUTER_AGENT_PROMPT,
        user=user_message,
        max_tokens=max_tokens,
    )
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
                system=(system_prompt or ROUTER_AGENT_PROMPT) + "\n仅返回完整判断JSON，不要解释，不要增加输出字段。",
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
            valid_shape = (isinstance(payload, dict) and isinstance(payload.get("completed_steps"), list)
                           and all(isinstance(step, str) for step in payload["completed_steps"]))
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
        proposed,
        has_pa_card=has_pa_card,
        completed_steps=completed_steps,
    )
    return RouterDecision(
        target_module=_SHORT_TO_FULL[resolved],
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
