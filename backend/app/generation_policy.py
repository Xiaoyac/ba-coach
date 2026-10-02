"""Local per-request compute budget, never a consent or workflow decision."""
import re
import unicodedata
from urllib.parse import urlparse

FAST_ACKS = frozenset({"好", "好的", "好呀", "嗯", "嗯嗯", "可以", "行", "收到", "谢谢", "谢谢你",
                      "明白了", "我明白了", "知道了", "我知道了", "愿意", "我愿意", "同意", "我同意",
                      "对", "是的", "ok", "okay", "thanks", "你好", "您好", "hi", "hello"})


def is_simple_ack(value: str) -> bool:
    # Full utterance only. Never classify "好的，但我不想活了" by its prefix.
    if not isinstance(value, str) or len(value) > 40:
        return False
    clean = unicodedata.normalize("NFKC", value).casefold().strip()
    return clean.strip(" .。!！,，;；") in FAST_ACKS


def native_thinking_options(settings, provider: str, *, enabled: bool, model: str | None = None) -> dict:
    """Use the selected model's wire format for both on and off requests."""
    model_name = str(model or getattr(settings, f"{provider}_model", "") or "").casefold()
    base_url = str(getattr(settings, f"{provider}_base_url", "") or "").casefold()
    if is_ark_kimi(settings, provider, model=model):
        # K3 on Ark Coding always reasons. An old "off" preference means
        # minimum effort, never an unsupported thinking/enable_thinking flag.
        return {"reasoning_effort": "low"}
    # DashScope's OpenAI-compatible Qwen endpoints do not use DeepSeek's
    # ``thinking: {type: ...}`` extension.  They require the Qwen wire field
    # ``enable_thinking``. In particular, Qwen3.8 defaults to thinking, so an
    # unrecognised off switch can consume a classifier's entire output budget.
    # DashScope Kimi rejects Qwen-specific thinking_budget.
    if "dashscope.aliyuncs.com" in base_url and model_name.startswith("kimi"):
        return {"enable_thinking": enabled}
    qwen_compatible = model_name.startswith("qwen") or "dashscope.aliyuncs.com" in base_url
    if qwen_compatible:
        options = {"enable_thinking": enabled}
        if enabled:
            options["thinking_budget"] = int(getattr(settings, "qwen_thinking_budget", 1024))
    else:
        options = {"thinking": {"type": "enabled" if enabled else "disabled"}}
    return options


def main_thinking_options(settings, provider: str, messages, *, enabled_override: bool | None = None) -> dict:
    latest = messages[-1] if messages else None
    content = getattr(latest, "source_content", latest.content) if latest is not None else ""
    # Inspect server-owned raw text, never parse user-supplied XML as metadata.
    fast = (getattr(settings, "chat_fast_ack_enabled", True) and latest is not None
            and latest.role == "user" and is_simple_ack(content))
    configured_effort = getattr(settings, f"{provider}_reasoning_effort", None)
    explicitly_disabled = configured_effort == "disabled"
    enabled = enabled_override if enabled_override is not None else not (fast or explicitly_disabled)
    if is_ark_kimi(settings, provider):
        effort = configured_effort if enabled else None
        return {"reasoning_effort": ark_kimi_effort(effort)}
    options = native_thinking_options(settings, provider, enabled=enabled)
    # Keep the same model, full system prompt/history/profile, and all guards.
    # This controls native hidden reasoning only; it never fabricates a reply.
    effort = configured_effort
    # DashScope Qwen uses enable_thinking/thinking_budget rather than the
    # DeepSeek reasoning_effort field; sending both can be rejected.
    if ("enable_thinking" not in options and enabled
            and effort and effort not in {"provider_default", "disabled"}):
        options["reasoning_effort"] = effort
    return options


def is_ark_kimi(settings, provider: str, *, model: str | None = None) -> bool:
    url = urlparse(str(getattr(settings, f"{provider}_base_url", "") or ""))
    name = str(model or getattr(settings, f"{provider}_model", "") or "").casefold()
    return (provider in {"deepseek", "doubao"} and name == "kimi-k3"
            and url.hostname == "ark.cn-beijing.volces.com"
            and url.path.rstrip("/") == "/api/coding/v3")


def ark_kimi_effort(effort: str | None) -> str:
    if effort in {None, "provider_default", "disabled"}:
        return "low"
    if effort not in {"low", "high", "max"}:
        raise ValueError("Ark Kimi-K3 supports low, high or max reasoning effort")
    return effort


def auxiliary_output_budget(settings, provider: str, model: str, requested: int) -> int:
    # max_tokens includes mandatory reasoning on this channel. Leave room for
    # the routing JSON / lead reply as well; do not extend request deadlines.
    return max(requested, 2048) if is_ark_kimi(settings, provider, model=model) else requested


def reply_effort_options(settings, provider: str, *, model: str) -> tuple[str, ...]:
    """Only advertise effort levels verified on this exact model/channel."""
    host = urlparse(str(getattr(settings, f"{provider}_base_url", "") or "")).hostname
    if (provider in {"deepseek", "doubao"} and model.casefold() == "kimi-k3"
            and (host == "dashscope.aliyuncs.com" or is_ark_kimi(settings, provider, model=model))):
        return ("low", "high", "max")
    return ()


def deep_reply_thinking_options(settings, provider: str, *, model: str,
                                effort: str | None = None) -> dict:
    """Native main-reply compute only; never a router or intervention policy.

    The deployed ``kimi-k3`` defaults to ``low``; an authorized per-conversation
    selection can override it only on the verified channel.
    This is an effort preference, not a wall-clock reasoning deadline.
    Other K3 aliases and Qwen3.8-Max retain their separate policies; provider
    channels can expose different supported effort levels.
    Qwen3.8 rejects reasoning_effort together with thinking_budget. For other
    Qwen models, omit our normal short budget and let the native default apply;
    do not invent an effort parameter for models that do not support one.
    """
    if effort is not None and effort not in reply_effort_options(settings, provider, model=model):
        raise ValueError("Unsupported main reply effort for this model/channel")
    if is_ark_kimi(settings, provider, model=model):
        return {"reasoning_effort": ark_kimi_effort(effort)}
    name = model.casefold()
    if provider == "claude":
        configured = getattr(settings, "claude_effort", "high")
        effort = configured if configured in {"xhigh", "max"} else "high"
        return {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
    options = native_thinking_options(settings, provider, enabled=True, model=model)
    if "enable_thinking" in options:
        options.pop("thinking_budget", None)
        if name == "kimi-k3":
            options["reasoning_effort"] = effort or "low"
        elif name.startswith("kimi-k3-") or name == "kimi/kimi-k3":
            options["reasoning_effort"] = "max"
        elif name == "qwen3.8-max" or name.startswith("qwen3.8-max-"):
            options["reasoning_effort"] = "xhigh"
    else:
        configured = getattr(settings, f"{provider}_reasoning_effort", None)
        # These providers already accept this setting in their normal path.
        # Unknown endpoints keep the known native enabled flag only.
        if provider == "doubao" or configured in {"low", "medium", "high"}:
            options["reasoning_effort"] = "high"
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
