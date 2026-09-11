"""Conservative, local-only retrieval gate. Never gates generation or risk screening."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping
import re
import unicodedata

GATE_VERSION = "rules-v1"

_GREETINGS = frozenset({"你好", "您好", "嗨", "哈喽", "早上好", "下午好", "晚上好", "hello", "hi"})
_ACKS = frozenset({"好", "好的", "好呀", "嗯", "嗯嗯", "明白了", "我明白了", "知道了", "我知道了",
                   "收到", "看到了", "谢谢", "谢谢你", "感谢", "ok", "okay", "thanks", "thank you"})
_PAUSES = frozenset({"再见", "拜拜", "先不聊了", "我暂时不聊了", "今天先到这里", "今天就到这里",
                     "我们下次再聊", "下次再聊", "暂停聊天", "结束聊天", "bye", "goodbye"})
_ACCOUNT = re.compile(
    r"(?:(?:请|请问|帮我|请帮我)\s*)?(?:我(?:想|要))?(?:在哪里|在哪|如何|怎么|怎样)?"
    r"(?:修改|更改|设置|重置|找回)(?:我的|我|账户的|账号的)?(?:昵称|头像|登录密码|账号密码|密码)(?:呢|呀|啊|吗)?"
    r"|(?:(?:请问|我想|我要)\s*)?(?:怎么|如何|在哪里|在哪)(?:登录|登陆|退出登录|注销账号)(?:呢|呀|啊)?"
)


@dataclass(frozen=True)
class RetrievalIntent:
    retrieve: bool
    reason: str
    version: str = GATE_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split()).strip(" .。!！,，;；")


def _has_context(state: Mapping[str, Any]) -> bool:
    # Include assistant turns: "shall I explain the method?" + "OK" needs retrieval.
    # Any uncertainty preserves search, even though this sacrifices skip coverage.
    memory = state.get("memory") or {}
    if any(value for key, value in memory.items() if key not in {"turn_count", "last_module", "sandbox_mode"}):
        return True
    if any(state.get(key) for key in ("clinical_context", "long_term_memory")) or any((state.get("module_steps") or {}).values()):
        return True
    social = _GREETINGS | _ACKS | _PAUSES
    for message in (state.get("chat_history") or [])[-6:]:
        content = message.get("content", "") if isinstance(message, dict) else message.content
        if content.strip() and _normalize(content) not in social:
            return True
    return False


def decide_retrieval(state: Mapping[str, Any], *, enabled: bool = True) -> RetrievalIntent:
    """Inspect ONLY the current utterance for exact skip intents; unknown => search.

    No substring blacklist, model call, embedding, IO, or modification of user text.
    History can veto acknowledgement skipping, never induce an exact greeting search.
    """
    if not enabled:
        return RetrievalIntent(True, "disabled")
    raw = state.get("user_input", "")
    if len(raw) > 160:
        return RetrievalIntent(True, "substantive_or_unknown")
    text = _normalize(raw)
    if not text:
        return RetrievalIntent(True, "substantive_or_unknown")
    if text in _GREETINGS:
        return RetrievalIntent(False, "greeting")
    # Whole utterance match: "谢谢，但我不想活了" must never match a social prefix.
    atoms = [part.strip() for part in re.split(r"[,，。;；!！]+", text) if part.strip()]
    if atoms and all(part in _ACKS | _PAUSES for part in atoms):
        if any(part in _PAUSES for part in atoms):
            return RetrievalIntent(False, "explicit_pause")
        if _has_context(state):
            return RetrievalIntent(True, "contextual_acknowledgement")
        return RetrievalIntent(False, "standalone_acknowledgement")
    if _ACCOUNT.fullmatch(text.rstrip("?？")):
        return RetrievalIntent(False, "account_navigation")
    # "继续", "可以", "那个呢" and all mixed/negated statements remain open.
    return RetrievalIntent(True, "substantive_or_unknown")
