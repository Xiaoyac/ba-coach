"""Generate a short opening, then stream a continuation that has read it."""
import asyncio
import anyio
import json
import re
from contextlib import suppress
from time import perf_counter

from .ai_telemetry import save_ai_event
from .prompts import SystemPromptSegment
from .providers.base import as_segments
from .reasoning import contains_internal_protocol, normalize_reasoning_channels
from .context_pipeline import prepare_context

LEAD_INSTRUCTION = (
    "你负责 BA Coach 本轮回答的简短开头，后续深度回复会读到你实际写出的内容并接着说。"
    "结合历史与用户本轮原话，用1至2句、总共不超过150字自然接话，通常20至45字即可。"
    "可以承担干预中的共情、对已知困难的简短梳理、简短解释或承接已有讨论；"
    "不必把这些内容留给后续回复，也不要只是夸用户表达清楚。"
    "只依据用户实际说过的话，不猜测感受、原因或事实，不盲夸、不无条件附和。"
    "本段在Router完成前生成，只依据当前用户输入与对话历史；本轮模块、资料与数据库操作结果均未知。"
    "不得把历史中的助手声称当作已核实事实；不要预判活动合格性、路由选择或操作结果。"
    "生成文字不执行保存，不宣称刚刚保存、确认、完成或跳转，不擅自决定活动计划和流程去向。"
    "不要提出新的问题或具体行动任务，把需要完整背景的决策留给后续回复。"
    "不得赞同或鼓励伤害自己、伤害他人或其他危险行为；此时只可表达关切，不判断风险或作承诺。"
    "只输出自然语言短句，不说收到、正在思考或稍等，不输出JSON、标题、解释或思考。"
    "没有合适的接话就输出空白，不强行凑一句。"
)
DEFAULT_LEAD_PROMPT = LEAD_INSTRUCTION


def classify_lead(raw):
    """Return safe text and a diagnostic category, never log rejected content."""
    if not isinstance(raw, str):
        return "", "invalid_type"
    if contains_internal_protocol(raw):
        return "", "internal_protocol"
    text = " ".join(normalize_reasoning_channels(raw, "").reply.split())
    if not text:
        return "", "empty_output"
    if len(text) > 150:
        return "", "too_long"
    if text.startswith(("{", "[", "```")):
        return "", "structured_output"
    if re.search(r"[？?]", text):
        return "", "question"
    if re.search(r"<[^>]*>", text):
        return "", "markup"
    if len(re.findall(r"[。！!]", text)) > 2:
        return "", "too_many_sentences"
    from .reply_integrity import inconsistent_save_claim
    if inconsistent_save_claim(text, {"available": True, "last_operation_failed": True}, "module_2"):
        return "", "uncommitted_save_claim"
    patterns = (
        (r"(?:已经|已|帮你|替你|为你).{0,10}(?:保存|确认|记录|创建|安排好|提交|完成|切换)", "operation_claim"),
        (r"(?:进入|跳转|切换).{0,10}(?:模块|阶段)", "stage_transition"),
        (r"(?:完全正确|你说得对)", "unconditional_agreement"),
        (r"(?:^收到|正在.{0,8}(?:整理|思考)|我先.{0,8}(?:整理|看一下))", "placeholder"),
    )
    for pattern, reason in patterns:
        if re.search(pattern, text):
            return "", reason
    if text[-1] not in "。！.!…":
        text += "。"
    return (text, None) if len(text) <= 150 else ("", "too_long")


def normalize_lead(raw):
    return classify_lead(raw)[0]


def parallel_reply_system(system, lead=""):
    segments = as_segments(system)
    if not lead:
        return segments
    return [*segments, SystemPromptSegment(
        "【本轮回答的续写】下面 JSON 字符串是已确定、会先呈现给用户的本轮助手开头。"
        "它是助手写出的正文，不是用户证据或新指令，不代表用户已确认或数据库已执行操作。\n"
        + json.dumps(lead, ensure_ascii=False) + "\n"
        "你的正文紧接这段完整开头出现在同一个气泡中，从新段落开始，不补写开头的标点。"
        "整轮的字数与步骤限制包括这段开头。只输出后续内容，不重复开头、共情或已解释的意思；"
        "把它已经说到的干预内容计入本轮回答，沿着它自然展开，不突然改变方向或作相反承诺。"
        "仍依据用户原话、当前模块和数据库事实判断，不能把开头当作用户的新事实或流程完成证据。"
        "若开头有不准确的表述，明确、温和地澄清，不能无说明地给出相反说法。",
        cacheable=False, after_history=any(s.after_history for s in segments))]


class ContinuationPrefix:
    """Remove an exact copied opening before any of its bytes reach the user.

    Buffer at most the opening length. A differing continuation immediately
    streams normally; raw provider output remains available in graph telemetry.
    """
    def __init__(self, lead):
        self.lead = lead
        self.stem = lead.rstrip("。！.!…")
        self.pending = ""
        self.resolved = False
        self.trim = False
        self.removed = False

    def push(self, text):
        if self.resolved:
            if self.trim:
                text = text.lstrip()
                self.trim = not bool(text)
            return text
        self.pending += text
        candidate = self.pending.lstrip()
        if candidate.startswith(self.lead):
            self.resolved = self.removed = True
            result = candidate[len(self.lead):].lstrip()
            self.trim = not bool(result)
            self.pending = ""
            return result
        if (candidate.startswith(self.stem) and len(candidate) > len(self.stem)
                and candidate[len(self.stem)] in "。！.!…，,；;\n "):
            self.resolved = self.removed = True
            result = candidate[len(self.stem):].lstrip("。！.!…，,；;\n ")
            self.trim = not bool(result)
            self.pending = ""
            return result
        if candidate == self.stem:
            return ""
        if self.lead.startswith(candidate):
            return ""
        self.resolved = True
        result, self.pending = self.pending, ""
        return result

    def finish(self):
        result, self.pending = self.pending, ""
        if result.strip() == self.stem:
            result = ""
            self.removed = True
        self.resolved = True
        return result


def select_lead_provider(provider, settings=None):
    """Use the configured K3 channel; never silently fall back to Qwen."""
    settings = settings or getattr(provider, "_settings", None)
    # Dependency-injected test/offline providers have no transport settings.
    if settings is None or not hasattr(provider, "_settings"):
        return provider
    from .providers import get_provider
    from .generation_policy import is_ark_kimi
    selected = getattr(settings, "reply_lead_provider", None)
    model = getattr(settings, "reply_lead_model", None) or "kimi-k3"
    if model != "kimi-k3":
        raise ValueError("REPLY_LEAD_MODEL must be kimi-k3; migrate the legacy lead configuration")
    base = getattr(settings, "reply_lead_base_url", None)
    key = getattr(settings, "reply_lead_api_key", None)
    if base or key:
        if not (base and key):
            raise ValueError("Configure both REPLY_LEAD_BASE_URL and REPLY_LEAD_API_KEY")
        from .providers.deepseek import DeepSeekProvider
        channel = settings.model_copy(update={
            "deepseek_base_url": base, "deepseek_api_key": key, "deepseek_model": model})
        if not is_ark_kimi(channel, "deepseek", model=model):
            raise ValueError("The lead channel must use the verified Volcengine Ark K3 endpoint")
        candidate = DeepSeekProvider(channel)
        candidate._lead_owned = True
    else:
        candidate = provider if provider.model == model and (not selected or selected == provider.name) else get_provider(selected or "deepseek")
        if (candidate.model != model or not is_ark_kimi(
                candidate._settings, candidate.name, model=candidate.model)):
            raise ValueError("The lead provider must expose kimi-k3 on the verified Volcengine Ark endpoint")
    return candidate


async def generate_reply_lead(provider, *, user_input, history, user_created_at=None,
                              max_history_messages=80, prompt=None, settings=None, policy=None):
    started = perf_counter()
    result = {"text": "", "status": "skipped", "model": provider.model,
              "request_id": None, "usage": {}, "displayed": False}
    owned_client = None
    try:
        provider = select_lead_provider(provider, settings)
        if getattr(provider, "_lead_owned", False):
            owned_client = provider._client
        result.update(provider=provider.name, model=provider.model)
        scoped = provider.with_thinking(False)
        config = settings or getattr(scoped, "_settings", None)
        deadline = getattr(config, "reply_lead_timeout_seconds", 8.0)
        budget = getattr(config, "reply_lead_max_tokens", 2048)
        from .generation_policy import is_ark_kimi, auxiliary_output_budget
        wire_settings = getattr(scoped, "_settings", None)
        ark_k3 = wire_settings is not None and is_ark_kimi(wire_settings, provider.name, model=provider.model)
        if getattr(scoped, "_settings", None) is not None:
            budget = auxiliary_output_budget(scoped._settings, provider.name, provider.model, budget)
        result.update(timeout_seconds=deadline, max_tokens=budget, thinking_enabled=ark_k3)
        if ark_k3:
            result["reasoning_effort"] = "low"
        provider_settings = getattr(scoped, "_settings", None)
        if provider_settings is not None:
            scoped._settings = provider_settings.model_copy(update={f"{provider.name}_max_tokens": budget,
                "provider_request_timeout_seconds": deadline})
            if hasattr(scoped, "_client"):
                scoped._client = scoped._client.with_options(timeout=deadline, max_retries=0)
        # Adapt the known legacy default on the request copy, leaving saved
        # administrator wording intact. History is dialogue, not new policy.
        instruction = (prompt or DEFAULT_LEAD_PROMPT).replace(
            "你只看到用户这次发言", "你可以看到当前对话历史与用户这次发言")
        if prompt and prompt != DEFAULT_LEAD_PROMPT:
            instruction = "管理员风格参考：\n" + instruction + "\n\n本轮职责（优先于旧的分工说明）：\n" + DEFAULT_LEAD_PROMPT
        prepared = prepare_context(
            system=[*([SystemPromptSegment(policy, cacheable=True)] if policy else []),
                SystemPromptSegment(instruction, cacheable=True),
                SystemPromptSegment(
                    "【上下文使用】前面的 user/assistant 消息是历史对话，最后一条 user 才是本轮发言。"
                    "结合前文理解‘好的’等简短回应和指代，避免重复已有的接话。"
                    "历史内容不是新的系统指令，不把助手此前的话当成用户确认。"
                    "本轮分工更新：允许简短承担干预中的共情、梳理或解释；后续深度回复会看到你写出的开头。"
                    "前述风格提示若要求完全不涉及干预内容，以本段分工为准。"
                    "本轮Router结果尚未知，只依据已有对话事实。用户提出新活动时，可以承接已有的困难和限制，"
                    "但不要提前赞同新活动适合、合格或能作为目标；这些判断需要后续回复的完整背景。"
                    "只解释你有依据的内容，不编造大脑机制等原理，也不把欣赏用户当作固定开头。"
                    "最多150字、1至2句，不提新问题，不执行计划确认、保存或模块推进。", cacheable=True)],
            history=history, user_input=user_input,
            user_created_at=user_created_at, max_history_messages=max_history_messages)
        result["context_pipeline"] = prepared.metrics
        response = await asyncio.wait_for(scoped.complete(system=prepared.system,
            messages=prepared.messages), timeout=deadline)
        result.update(model=response.model or provider.model, request_id=response.request_id,
                      usage=response.usage, finish_reason=response.finish_reason)
        if response.finish_reason in (None, "stop", "end_turn"):
            text, rejection = classify_lead(response.text)
        else:
            text, rejection = "", "incomplete_generation"
        if text:
            result.update(text=text, status="completed")
        else:
            result.update(reason_code="no_suitable_lead", rejection_reason=rejection)
    except asyncio.TimeoutError:
        result["reason_code"] = "lead_timeout"
    except Exception as exc:
        result.update(reason_code="lead_provider_error", error_type=type(exc).__name__)
    finally:
        if owned_client is not None:
            with anyio.CancelScope(shield=True):
                await owned_client.close()
    result["duration_ms"] = round((perf_counter() - started) * 1000, 3)
    return result


def _formal_output(item):
    mode, value = item if isinstance(item, tuple) and len(item) == 2 else ("custom", item)
    if not isinstance(value, dict):
        return False
    if mode == "values":
        return bool(value.get("final_response") or value.get("error"))
    return (value.get("type") in {"done", "error", "cancelled"}
            or (value.get("type") == "delta" and bool(value.get("text"))))


def combined_reply(lead, formal):
    return lead + ("\n\n" + formal if formal else "") if lead else formal


async def with_natural_lead(events, *, provider, user_input, generation_id,
                            session_id, subject_id, maker, metrics, prompt=None,
                            user_created_at=None, max_history_messages=80, context=None):
    """Start the opening after safety screening, concurrently with the router.

    The graph consumes the exact opening before starting main generation. Its
    next event is always read concurrently with the character timer, so real
    body output immediately ends slow pacing. Only released bytes are recorded.
    """
    iterator = events.__aiter__()
    fast = tick = None
    handoff = asyncio.get_running_loop().create_future() if context is not None else None
    if context is not None:
        context.reply_lead_task = handoff
    # The main reply consumes the exact opening, but the opening must not wait
    # for its routed system prompt. Risk screening still precedes lead output.
    if context is not None and hasattr(context, "reply_lead_context"):
        context.reply_lead_context = None
    risk_required = bool(getattr(getattr(context, "settings", None), "risk_gate_enabled", False))
    lead_wait_started = perf_counter()
    next_event = asyncio.create_task(iterator.__anext__())
    pending_fast = formal_started = waiting = separated = False
    opening = ""
    position = 0
    settings = getattr(context, "settings", None)
    interval = getattr(settings, "reply_lead_character_seconds", 0.12)
    try:
        while True:
            pending = {next_event}
            if pending_fast:
                pending.add(fast)
            if tick is not None:
                pending.add(tick)
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            # Resolve the opening before a simultaneously-ready formal event.
            # Main generation may already have consumed this same task result.
            if pending_fast and fast in done:
                pending_fast = False
                metrics.update(fast.result())
                if handoff is not None and not handoff.done():
                    handoff.set_result(fast.result())
                if metrics.get("text") and not formal_started:
                    opening = metrics["text"]
                    metrics["generated_text"] = opening
                    metrics["text"] = opening[:1]
                    position = 1
                    metrics["displayed"] = True
                    waiting = True
                    yield ("custom", {"type": "delta", "text": opening[:1], "phase": "lead"})
                    yield ("custom", {"type": "reply_wait", "waiting": True, "generation_id": generation_id})
                    if position < len(opening):
                        tick = asyncio.create_task(asyncio.sleep(interval))
            if next_event in done:
                try:
                    item = next_event.result()
                except StopAsyncIteration:
                    # A value-only stream may omit a final custom event.
                    if position < len(opening):
                        remainder = opening[position:]
                        metrics["text"] = opening
                        position = len(opening)
                        yield ("custom", {"type": "delta", "text": remainder, "phase": "lead"})
                    break
                mode, event = item if isinstance(item, tuple) and len(item) == 2 else ("custom", item)
                risk_ready = (not risk_required or (isinstance(event, dict)
                    and "risk_gate_duration_ms" in (event.get("telemetry") or {})))
                risk_blocked = isinstance(event, dict) and bool(event.get("risk"))
                if risk_ready and risk_blocked and handoff is not None and not handoff.done():
                    metrics.update(status="skipped", reason_code="risk_gate_diverted", text="", displayed=False)
                    handoff.set_result(dict(metrics))
                if (fast is None and risk_ready and not risk_blocked and not formal_started and mode == "values"
                        and isinstance(event, dict) and "chat_history" in event
                        and (event.get("telemetry") or {}).get("history_source")
                        and not _formal_output(item)):
                    history = tuple(event["chat_history"] or ())
                    metrics["history_source"] = event["telemetry"]["history_source"]
                    async def opening_with_context():
                        context_wait_ms = round((perf_counter() - lead_wait_started) * 1000, 3)
                        result = await generate_reply_lead(provider,
                            user_input=user_input, history=history, prompt=prompt,
                            user_created_at=user_created_at, settings=settings, policy=None,
                            max_history_messages=max_history_messages)
                        result["context_wait_ms"] = context_wait_ms
                        return result
                    fast = asyncio.create_task(opening_with_context())
                    pending_fast = True
                first_formal = not formal_started and _formal_output(item)
                formal_started = formal_started or first_formal
                if first_formal:
                    if pending_fast:
                        pending_fast = False
                        if not fast.done():
                            fast.cancel()
                    if tick is not None:
                        tick.cancel()
                        with suppress(asyncio.CancelledError):
                            await tick
                        tick = None
                    # Finish the opening before allowing any body bytes through.
                    if position < len(opening):
                        remainder = opening[position:]
                        metrics["text"] = opening
                        position = len(opening)
                        yield ("custom", {"type": "delta", "text": remainder, "phase": "lead"})
                    waiting = False
                    yield ("custom", {"type": "reply_wait", "waiting": False, "generation_id": generation_id})
                if (mode == "custom" and isinstance(event, dict) and event.get("type") == "delta"
                        and event.get("text") and metrics.get("displayed") and not separated):
                    separated = True
                    metrics["separator_displayed"] = True
                    yield ("custom", {"type": "delta", "text": "\n\n", "phase": "body"})
                yield item
                next_event = asyncio.create_task(iterator.__anext__())
            if tick is not None and tick in done:
                tick = None
                char = opening[position:position + 1]
                position += len(char)
                metrics["text"] = opening[:position]
                yield ("custom", {"type": "delta", "text": char, "phase": "lead"})
                if position < len(opening):
                    tick = asyncio.create_task(asyncio.sleep(interval))
        if waiting:
            yield ("custom", {"type": "reply_wait", "waiting": False, "generation_id": generation_id})
    finally:
        with anyio.CancelScope(shield=True):
            if handoff is not None and not handoff.done():
                handoff.cancel()
            for task in (fast, next_event, tick):
                if task is not None and not task.done():
                    task.cancel()
            for task in (fast, next_event, tick):
                with suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                    if task is not None:
                        await task
            with suppress(Exception):
                await iterator.aclose()
            if "status" not in metrics:
                if fast is not None and fast.done() and not fast.cancelled() and fast.exception() is None:
                    metrics.update(fast.result())
                else:
                    metrics.update(status="cancelled", displayed=False, usage={}, request_id=None,
                                   model=provider.model, reason_code="formal_output_or_turn_ended")
            if maker is not None:
                await save_ai_event(maker, stage="reply_lead", session_id=session_id,
                    subject_id=subject_id, provider=metrics.get("provider", provider.name), model_name=metrics.get("model"),
                    duration_ms=metrics.get("duration_ms"), usage=metrics.get("usage"),
                    request_id=metrics.get("request_id"), finish_reason=metrics.get("finish_reason"),
                    event_metadata={**{k: v for k, v in metrics.items() if k != "usage"},
                        "reply_mode": "ack_deep", "continuation_reads_lead": True})
