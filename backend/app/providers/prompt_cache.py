"""Provider-side prefix reuse without changing prompt characters or roles.

Chat Completions remains stateless: every request includes the full context.
Stable system blocks keep the same cache boundaries; changing state is an
uncached suffix. No old snapshots are accumulated or fed back to the model.
"""
from urllib.parse import urlsplit

from .base import SystemPrompt, as_segments, as_text


def ordered_messages(system: SystemPrompt, messages: list[dict]) -> list[dict]:
    """Preserve trusted system placement through plain and native-tool calls.

    The insertion point is the final user turn, not the final message: tool
    continuation frames must stay after it and retain their exact ordering.
    No tags in user text are interpreted as transport instructions.
    """
    segments = as_segments(system)
    prefix = [s for s in segments if not s.after_history]
    tail = [s for s in segments if s.after_history]
    if not tail:
        return [{"role": "system", "content": as_text(system)}, *messages]
    boundary = next((i for i in range(len(messages) - 1, -1, -1)
                     if messages[i]["role"] == "user"), None)
    if boundary is None:
        raise ValueError("tail system requires a current user turn")
    return [*([{"role": "system", "content": as_text(prefix)}] if prefix else []),
            *messages[:boundary], {"role": "system", "content": as_text(tail)},
            *messages[boundary:]]


def system_messages(system: SystemPrompt, *, settings, model: str) -> list[dict]:
    # Enable only the official endpoint/model combination verified in production.
    # Other compatible gateways keep their original string payload.
    if (not getattr(settings, 'qwen_prompt_cache_enabled', True)
            or urlsplit(getattr(settings, 'deepseek_base_url', '')).hostname != 'dashscope.aliyuncs.com'
            or model.lower() != 'qwen3.8-flash'):
        return [{'role': 'system', 'content': as_text(system)}]
    segments = as_segments(system)
    prefix = []
    for index, segment in enumerate(segments):
        if not segment.cacheable:
            break
        prefix.append(index)
    stable = as_text(segments[:len(prefix)])
    if not stable:
        return [{'role': 'system', 'content': as_text(system)}]
    # Qwen 3.8's compatible endpoint cached the entire message when stable and
    # volatile text blocks shared one system message. A separate, unmarked
    # system suffix was verified to retain prefix hits with changing state and
    # 60 history messages. Keep all state at its original instruction level.
    # Use one boundary; do not assume content-level checkpoints work here.
    messages = [{'role': 'system', 'content': [{
        'type': 'text', 'text': stable, 'cache_control': {'type': 'ephemeral'},
    }]}]
    if len(prefix) < len(segments):
        messages.append({'role': 'system', 'content': '\n\n' + as_text(segments[len(prefix):])})
    return messages
