"""One synthetic live-model smoke request. No accounts, conversations or DB writes."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.db import get_sessionmaker, dispose_db
from app.knowledge_mediator import mediate_knowledge
from app.prompt_store import effective_mediator_prompt
from app.providers import get_provider
from app.retrieval import KnowledgeChunk


async def main():
    settings = get_settings()
    async with get_sessionmaker()() as db:
        prompt = await effective_mediator_prompt(db)
    debug = {}
    selected, _, metrics = await mediate_knowledge(
        state={"user_input":"我想先尝试每天散步十分钟，可以怎样开始？"},
        module="module_2",
        knowledge=[KnowledgeChunk("synthetic-ba", "活动安排可以从可行的小步骤开始，根据体力和偏好调整。", "synthetic-probe")],
        provider=get_provider("deepseek"), settings=settings, prompt=prompt, debug_output=debug,
    )
    print(json.dumps({"status":metrics["status"],"reason":metrics["reason"],
        "duration_ms":metrics["duration_ms"],"model":metrics.get("model"),
        "reasoning_returned":bool(debug.get("reasoning_content")),
        "reasoning_chars":len(debug.get("reasoning_content") or ""),
        "approved_count":len(selected), "timeout_seconds":settings.knowledge_mediator_timeout_seconds,
        "database_writes":False},ensure_ascii=False))
    await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
