"""Read-only production-prompt smoke test with fictional, non-persisted input."""
import asyncio
import json
from app.db import get_sessionmaker, dispose_db
from app.prompt_store import effective_prompt_pair
from app.prompts import build_system_prompt
from app.providers import get_provider
from app.reply_workflow import workflow_prompt
from app.schemas import Message


async def main():
    try:
        async with get_sessionmaker()() as db:
            global_prompt, module_prompt = await effective_prompt_pair(db, "module_2")
        prompt = build_system_prompt("module_2", global_prompt=global_prompt, module_prompt=module_prompt)
        prompt += "\n" + workflow_prompt({"available": True, "current_module": "module_2",
            "goal_selected": False, "plan_confirmed": False, "flow_status": "active"})
        result = await get_provider().complete(system=prompt, messages=[Message(role="user",
            content="我刚完成M1，已经核对确认了。我不知道PA是什么，还没想好任何活动，可以先解释吗？不要要求我先建目标。")])
        print(json.dumps({"model": result.model, "reply": result.text, "usage": result.usage,
                          "database_writes": False}, ensure_ascii=False))
    finally:
        await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
