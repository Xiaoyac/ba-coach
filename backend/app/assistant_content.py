"""Remove only the known message transport envelope from assistant prose."""
import re
from dataclasses import dataclass
from html import unescape

_TIME = r"(?:unknown|\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?)"
_HEADER = re.compile(
    rf"\A\s*(?P<message><message>\s*)?<datetime>{_TIME}</datetime>\s*",
)
_TIME_VALUE = re.compile(rf"{_TIME}\Z")
_OPENERS = ("<message>", "<datetime>")


def _unfinished_header(text: str) -> bool:
    """Recognize a truncated transport header, never an arbitrary XML tag."""
    value = text.lstrip()
    if not value:
        return False
    if any(opener.startswith(value) for opener in _OPENERS):
        return True
    if value.startswith("<message>"):
        value = value[len("<message>"):].lstrip()
        if "<datetime>".startswith(value):
            return True
    if not value.startswith("<datetime>"):
        return False
    stamp = value[len("<datetime>"):]
    if "<" in stamp:
        stamp, tail = stamp.split("<", 1)
        return bool(_TIME_VALUE.fullmatch(stamp) and "</datetime>".startswith("<" + tail))
    return "unknown".startswith(stamp) or bool(re.fullmatch(r"\d[\dT:.+Z-]*", stamp))


def _strip_content_tail(body: str, *, message: bool) -> str:
    """Remove complete or interrupted closing tags only at the outside edge."""
    closers = ("</content>", "</message>") if message else ("</content>",)
    for _ in range(2):
        trimmed = body.rstrip()
        start = trimmed.rfind("<")
        if start < 0:
            break
        suffix = trimmed[start:]
        if not suffix.startswith("</") or not any(closer.startswith(suffix) for closer in closers):
            break
        body = trimmed[:start]
    return body


def unwrap_assistant_message(text: str) -> str:
    for _ in range(8):
        match = _HEADER.match(text)
        if not match:
            return "" if _unfinished_header(text) else text
        body = text[match.end():]
        has_message = match.group("message") is not None
        if body.startswith("<content>"):
            text = unescape(_strip_content_tail(body[len("<content>"):], message=has_message))
        elif "<content>".startswith(body):
            return ""
        elif has_message:
            # A message tag without the known content boundary may be prose
            # explaining XML; do not guess at its meaning or remove it.
            return text
        else:
            # A standalone leading transport datetime is model metadata, not
            # a trustworthy date. Preserve the following prose verbatim.
            text = body
    return text


@dataclass
class AssistantEnvelopeStream:
    """Hold only a possible leading envelope until it can be safely unwrapped."""
    pending: str = ""
    passthrough: bool = False

    def push(self, text: str) -> list[str]:
        if self.passthrough:
            return [text] if text else []
        self.pending += text
        preview = self.pending.lstrip()
        if not preview or any(opener.startswith(preview) or preview.startswith(opener)
                              for opener in _OPENERS):
            return []
        self.passthrough = True
        result, self.pending = self.pending, ""
        return [result]

    def finish(self) -> list[str]:
        result, self.pending = unwrap_assistant_message(self.pending), ""
        return [result] if result else []
