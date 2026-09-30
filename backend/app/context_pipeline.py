"""LangChain context assembly shared by ordinary and crisis generation.

LangGraph owns workflow state; the database/session store owns persistence.
This layer owns the bounded message window and typed chat prompt. Provider
SDKs still receive their native cache-tagged system segments and text messages.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import version
from time import perf_counter

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, trim_messages
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from .prompts import SystemPromptSegment
from .schemas import Message
from .conversation_time import TEMPORAL_RULES, dated_content, request_time_context
from .assistant_content import unwrap_assistant_message


LANGCHAIN_CORE_VERSION = version("langchain-core")
_PROMPT = ChatPromptTemplate.from_messages([
    MessagesPlaceholder("system_segments"),
    MessagesPlaceholder("history"),
    ("human", "{user_input}"),
])


def _chat_messages(messages: Sequence[Message], *, annotate_time: bool = False) -> list[BaseMessage]:
    # Deliberately exclude stored reasoning, routing traces and display metadata.
    # Index IDs let history trimming select original records without losing UI
    # metadata in the session store or confusing duplicate message contents.
    return [
        (HumanMessage if message.role == "user" else AIMessage)(
            content=(unwrap_assistant_message(message.content) if message.role == "assistant"
                     else dated_content(message.content, message.created_at)
                     if annotate_time else message.content), id=str(index)
        )
        for index, message in enumerate(messages)
    ]


def trim_history(messages: Sequence[Message], max_messages: int) -> list[Message]:
    """Keep the newest whole messages, starting at a user turn after overflow.

    The existing limit is a message count, not a token budget. Short histories
    retain the assistant's opening greeting. Once the window overflows, use
    actual roles instead of assuming even/odd indices identify turn boundaries.
    """
    if max_messages < 0:
        raise ValueError("max_messages must be non-negative")
    if max_messages == 0:
        return []
    selected = trim_messages(
        _chat_messages(messages),
        max_tokens=max_messages,
        token_counter=len,
        strategy="last",
        start_on="human" if len(messages) > max_messages else None,
        allow_partial=False,
    )
    return [messages[int(message.id)] for message in selected]


@dataclass(frozen=True)
class PreparedContext:
    system: list[SystemPromptSegment]
    messages: list[Message]
    metrics: dict[str, str | int]


def prepare_context(
    *,
    system: Sequence[SystemPromptSegment],
    history: Sequence[Message],
    user_input: str,
    max_history_messages: int,
    user_created_at: datetime | None = None,
    current_time: datetime | None = None,
) -> PreparedContext:
    """Compile trusted system blocks, bounded history and the current user turn.

    Concrete SystemMessage objects and placeholder values keep braces/JSON in
    administrator prompts and user content literal. The current input is added
    after trimming, so even a very long current message is never truncated.
    Formatting is local; it adds no model call or external tracing service.
    """
    started = perf_counter()
    kept = trim_history(history, max_history_messages)
    # Older administrator overrides may still describe the retired envelope.
    # Adapt only request copies; preserve saved prompts and user content.
    system = [SystemPromptSegment(
        segment.text.replace("<user_message>", "<content>").replace(
            "</user_message>", "</content>"), cacheable=segment.cacheable
    ) for segment in system]
    system.append(SystemPromptSegment(TEMPORAL_RULES, cacheable=False))
    system.append(SystemPromptSegment(request_time_context(
        user_created_at=user_created_at, current_time=current_time), cacheable=False))
    current = Message(role="user", content=user_input, created_at=user_created_at)
    prompt = _PROMPT.format_prompt(
        system_segments=[
            SystemMessage(content=segment.text, additional_kwargs={"cacheable": segment.cacheable})
            for segment in system
        ],
        history=_chat_messages(kept, annotate_time=True),
        user_input=dated_content(user_input, user_created_at),
    )
    compiled_system = []
    compiled_messages = []
    for message in prompt.to_messages():
        if isinstance(message, SystemMessage):
            compiled_system.append(SystemPromptSegment(
                message.content, cacheable=message.additional_kwargs["cacheable"]
            ))
        else:
            # Keep raw content and immutable timestamp in private/request
            # metadata. Fast acknowledgements must not parse datetime markup.
            source = kept[len(compiled_messages)] if len(compiled_messages) < len(kept) else current
            compiled_messages.append(Message(role=source.role, content=source.content,
                created_at=source.created_at).for_provider(message.content))
    return PreparedContext(
        system=compiled_system,
        messages=compiled_messages,
        metrics={
            "engine": "langchain",
            "time_format": "server_clock_user_message_v4",
            "langchain_core_version": LANGCHAIN_CORE_VERSION,
            "history_messages_before": len(history),
            "history_messages_kept": len(kept),
            "history_messages_dropped": len(history) - len(kept),
            "max_history_messages": max_history_messages,
            "system_segments": len(compiled_system),
            "cacheable_system_segments": sum(segment.cacheable for segment in compiled_system),
            "provider_messages": len(compiled_messages),
            "duration_ms": int((perf_counter() - started) * 1000),
        },
    )
