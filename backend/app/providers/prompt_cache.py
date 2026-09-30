"""Provider-side prefix reuse without changing prompt characters or roles.

Chat Completions remains stateless: every request includes the full context.
Stable system blocks keep the same cache boundaries; changing state is an
uncached suffix. No old snapshots are accumulated or fed back to the model.
"""
from urllib.parse import urlsplit

from .base import SystemPrompt, as_segments, as_text


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
