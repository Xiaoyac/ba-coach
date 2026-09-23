"""Synthetic smoke checks for the isolated memory-anchor release."""

from app.graph.nodes import CONTEXT_ANCHOR_MESSAGES, CONTEXT_ANCHOR_TOTAL_CHARS
from app.prompts import _memory_block


assert CONTEXT_ANCHOR_MESSAGES == 12
assert CONTEXT_ANCHOR_TOTAL_CHARS == 2600
rendered = _memory_block({"conversation_anchor": "用户：我和男朋友吵架了"})
assert "早期对话锚点" in rendered
assert "当前说法为准" in rendered
print("MEMORY_ANCHOR_SYNTHETIC_SMOKE_OK")
