"""The second model slot: DeepSeek 4.1 Flash 1017 with thinking always off.

The slot keeps the stored preference key ``doubao`` so existing account rows
and their CHECK constraint stay valid. It rides the DeepSeek (Ark Coding)
credentials; only the model and the thinking switch differ.
"""

from __future__ import annotations

from copy import copy

from ..config import Settings
from ..generation_policy import FLASH_MODEL
from .base import LLMProvider
from .deepseek import DeepSeekProvider


class FlashProvider(DeepSeekProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.model_copy(update={
            "deepseek_model": FLASH_MODEL, "deepseek_router_model": FLASH_MODEL,
            "deepseek_reasoning_effort": "disabled"}))

    def with_thinking(self, enabled: bool | None) -> LLMProvider:
        return super().with_thinking(False)

    def reply_effort_options(self) -> tuple[str, ...]:
        return ()

    def with_deep_reply(self, effort: str | None = None) -> LLMProvider:
        # 深度回复 would turn native reasoning back on; this slot never thinks.
        return copy(self)
