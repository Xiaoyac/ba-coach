"""Local per-request compute budget, never a consent or workflow decision."""
import re
import unicodedata

FAST_ACKS = frozenset({"好", "好的", "好呀", "嗯", "嗯嗯", "可以", "行", "收到", "谢谢", "谢谢你",
                      "明白了", "我明白了", "知道了", "我知道了", "愿意", "我愿意", "同意", "我同意",
                      "对", "是的", "ok", "okay", "thanks", "你好", "您好", "hi", "hello"})


def is_simple_ack(value: str) -> bool:
    # Full utterance only. Never classify "好的，但我不想活了" by its prefix.
    if not isinstance(value, str) or len(value) > 40:
        return False
    clean = unicodedata.normalize("NFKC", value).casefold().strip()
    return clean.strip(" .。!！,，;；") in FAST_ACKS


def main_thinking_options(settings, provider: str, messages) -> dict:
    latest = messages[-1] if messages else None
    fast = (getattr(settings, "chat_fast_ack_enabled", True) and latest is not None
            and latest.role == "user" and is_simple_ack(latest.content))
    configured_effort = getattr(settings, f"{provider}_reasoning_effort", None)
    explicitly_disabled = configured_effort == "disabled"
    model_name = str(getattr(settings, f"{provider}_model", "") or "").casefold()
    base_url = str(getattr(settings, f"{provider}_base_url", "") or "").casefold()
    # DashScope's OpenAI-compatible Qwen endpoints do not use DeepSeek's
    # ``thinking: {type: ...}`` extension.  They require the Qwen wire field
    # ``enable_thinking``; when enabled, thinking_budget is required by some
    # Qwen hybrid deployments and is harmless for compatible Qwen models.
    qwen_compatible = model_name.startswith("qwen") or "dashscope.aliyuncs.com" in base_url
    if qwen_compatible:
        options = {"enable_thinking": not (fast or explicitly_disabled)}
        if options["enable_thinking"]:
            options["thinking_budget"] = int(getattr(settings, "qwen_thinking_budget", 1024))
    else:
        options = {"thinking": {"type": "disabled" if fast or explicitly_disabled else "enabled"}}
    # Keep the same model, full system prompt/history/profile, and all guards.
    # This controls native hidden reasoning only; it never fabricates a reply.
    effort = configured_effort
    # DashScope Qwen uses enable_thinking/thinking_budget rather than the
    # DeepSeek reasoning_effort field; sending both can be rejected.
    if (not qwen_compatible and not fast and not explicitly_disabled
            and effort and effort != "provider_default"):
        options["reasoning_effort"] = effort
    return options


def is_dialogue_confirmation(state) -> bool:
    """Only skip retrieval for a recognized immediate confirmation question.

    An invitation to explain BA still needs retrieval. Unknown contexts keep
    the original conservative path. This function never confirms a DB record.
    """
    if not is_simple_ack(state.get("user_input", "")):
        return False
    history = state.get("chat_history") or []
    if not history:
        return False
    latest = history[-1]
    role = latest.get("role") if isinstance(latest, dict) else latest.role
    body = latest.get("content", "") if isinstance(latest, dict) else latest.content
    if role != "assistant":
        return False
    # Multi-question or explanation invitations stay on the retrieval path.
    if len(re.findall(r"[？?]", body)) > 1 or re.search(r"解释|介绍|讲解|讲讲|原理|两分钟规则|TRAP|TRAC|为什么|怎么理解|了解|听听|听我|说说|理论|技巧|策略|机制|科普", body, re.I):
        return False
    question = re.split(r"[。！!\n]", body)[-1].strip()
    if not question:
        return False
    return bool(re.search(
        r"(?:愿意|同意|确认|合适|可行|怎么样).{0,30}(?:安排|计划|试试|记录)|"
        r"(?:安排|计划|记录方式|记录方法|总结|理解).{0,30}(?:同意|确认|合适吗|可行吗|对吗|符合|愿意)|"
        r"愿意.{0,15}(?:进入|开始).{0,8}目标设定", question))
