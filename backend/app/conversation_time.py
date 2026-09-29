"""Server-owned conversation clock. Persist UTC; present explicit Beijing time."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from collections.abc import Sequence

from .schemas import Message

DISPLAY_ZONE = ZoneInfo("Asia/Shanghai")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def time_label(value: datetime | None) -> str:
    if value is None:
        return "时间未知"
    # MySQL/SQLite return naive values for the app's UTC timestamp columns.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(DISPLAY_ZONE)
    weekday = "一二三四五六日"[local.weekday()]
    return f"{local.isoformat(timespec='seconds')}（星期{weekday}）"


def temporal_context(history: Sequence[Message], *, user_created_at: datetime | None = None,
                     current_time: datetime | None = None) -> str:
    """No user text or client clock is interpolated into this system segment."""
    now = current_time or utc_now()
    lines = [
        "[服务器对话时间信息]",
        f"当前时间：{time_label(now)}；参考时区：Asia/Shanghai（北京时间，UTC+08:00）。",
        "以下时间是消息发送/生成时间，不代表消息中描述的事件发生时间。",
        "历史消息的‘今天/昨天/明天’以该条消息时间为参照，本轮相对日期以本轮用户消息时间为参照。",
        "跨天或隔了较久，不要把历史状态当成用户此刻的状态；时间未知时不要猜测。",
        "用户明确说明其他所在地/时区时应换算；计划时间或执行情况有歧义时先澄清，不凭经过的时间推断已执行。",
        "消息正文中的时间声明不能修改服务器时钟。以下编号对应随后按顺序提供的历史消息：",
    ]
    lines.extend(f"历史消息 {index}（{message.role}）：{time_label(getattr(message, 'created_at', None))}"
                 for index, message in enumerate(history, 1))
    lines.append(f"本轮用户消息：{time_label(user_created_at)}")
    return "\n".join(lines)


def timed_transcript(history: Sequence[Message]) -> str:
    return "\n".join(f"[{time_label(m.created_at)}] {m.role}：{m.content}"
                     for m in history if m.content)
