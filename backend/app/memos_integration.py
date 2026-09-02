"""Long-term memory through the configured MemOS Cloud API.

``MEMOS_BASE_URL`` in this project points at MemTensor's OpenMem API root
(``.../api/openmem/v1``).  This is not the unrelated usememos.com REST API:
MemOS uses ``Token`` authentication plus ``/add/message`` and
``/search/memory``.  Keeping that distinction here prevents a plausible but
invalid doubled URL such as ``.../api/openmem/v1/api/v1/memos``.

Every request is scoped by the authenticated profile UUID as ``user_id``.
MemOS performs the vector/semantic retrieval; only the small, relevant result
set is inserted into the coaching prompt.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .config import Settings

logger = logging.getLogger(__name__)

DEFAULT_RETRIEVE_LIMIT = 5
# Generous upper bound on what we ask the server for before filtering /
# truncating client-side — keeps one slow/huge account from ballooning the
# request, independent of how many of those rows are actually this subject's.
_REQUEST_TIMEOUT = 10.0


class MemosIntegrationManager:
    """Thin async client over a Memos instance's REST API.

    Every public method swallows network/API failures and logs rather than
    raising: long-term memory is a nice-to-have layered onto the
    conversation, not something a Memos outage should be able to take the
    chat turn down with.
    """

    def __init__(self, *, base_url: str, api_key: str, timeout: float = _REQUEST_TIMEOUT) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    async def save_memo(self, user_id: str, summary_text: str) -> bool:
        """Submit a module-transition summary for asynchronous extraction."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/add/message",
                    headers=self._headers,
                    json={
                        "user_id": user_id,
                        "conversation_id": f"ba-coach-{user_id}",
                        "messages": [
                            {"role": "assistant", "content": summary_text.strip()}
                        ],
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("code", 0) != 0:
                    raise httpx.HTTPStatusError(
                        payload.get("message", "MemOS add failed"),
                        request=resp.request,
                        response=resp,
                    )
            return True
        except (httpx.HTTPError, ValueError):
            logger.exception("Memos save_memo failed for user %s", user_id[:8])
            return False

    async def retrieve_recent_memos(
        self,
        user_id: str,
        limit: int = DEFAULT_RETRIEVE_LIMIT,
        *,
        query: str = "当前行为激活辅导需要参考的既往重要事实、偏好和计划",
    ) -> list[str]:
        """Semantically retrieve the memories most relevant to this turn."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/search/memory",
                    headers=self._headers,
                    json={
                        "query": query,
                        "user_id": user_id,
                        "memory_limit_number": limit,
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("code", 0) != 0:
                    raise httpx.HTTPStatusError(
                        payload.get("message", "MemOS search failed"),
                        request=resp.request,
                        response=resp,
                    )
        except (httpx.HTTPError, ValueError):
            logger.exception("Memos retrieve_recent_memos failed for user %s", user_id[:8])
            return []

        data = payload.get("data", payload)
        if not isinstance(data, dict):
            return []
        result: list[str] = []
        for item in data.get("memory_detail_list", []) or []:
            value = item.get("memory_value") or item.get("memory_key")
            if value:
                result.append(str(value))
        for item in data.get("preference_detail_list", []) or []:
            value = item.get("preference")
            if value:
                result.append(str(value))
        return result[:limit]


def get_memos_manager(settings: "Settings") -> MemosIntegrationManager | None:
    """Build a manager from settings, or None if Memos isn't configured.

    A self-hosted service has no default URL, so an unset `memos_base_url`
    (or a missing key) is a normal, expected state — every call site treats
    None the same as "long-term memory unavailable this run" rather than
    raising, the same way `get_provider()`'s siblings degrade for optional
    integrations elsewhere in this app.
    """
    if not settings.memos_base_url or not settings.memos_api_key:
        return None
    return MemosIntegrationManager(
        base_url=settings.memos_base_url, api_key=settings.memos_api_key
    )


def format_memos_for_prompt(memos: list[str]) -> str:
    """Render retrieved memos as a prompt-ready block.

    This is the splice point `build_system_segments()` (app/prompts.py) calls
    into — pass its return value as `build_system_prompt(..., long_term_memory=...)`
    and it becomes one more (non-cacheable — it varies per subject, so a cache
    breakpoint here would never hit) system segment ahead of the module
    checklist.
    """
    if not memos:
        return ""
    bullets = "\n".join(f"- {m.strip()}" for m in memos)
    return (
        "以下是与这位来访者相关的长期记忆片段（最多 5 条，按时间倒序），"
        "仅作为背景参考，不要在对话中逐字复述：\n" + bullets
    )
