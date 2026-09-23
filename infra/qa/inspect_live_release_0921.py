"""Read-only production release and prompt audit; loads protected env on host."""
import asyncio
import hashlib
import json
from sqlalchemy import text
from app.db import get_sessionmaker, dispose_db
from app.prompt_store import effective_prompt_pair, effective_router_prompt


async def main():
    try:
        async with get_sessionmaker()() as db:
            global_prompt, module_prompt = await effective_prompt_pair(db, "module_2")
            router = await effective_router_prompt(db)
            rows = (await db.execute(text(
                "SELECT prompt_key, LENGTH(content) AS chars, updated_at "
                "FROM prompt_overrides ORDER BY prompt_key"))).mappings().all()
            uid = await db.scalar(text(
                "SELECT profile_uuid FROM user_accounts WHERE username='admin'"))
            runtime = (await db.execute(text(
                "SELECT c.id, r.current_module, r.active_goal_id, r.active_cycle_id, "
                "r.flow_status, r.last_transition_reason "
                "FROM conversations c JOIN conversation_runtime_states r "
                "ON r.conversation_id=c.id WHERE c.subject_id=:uid "
                "ORDER BY c.updated_at DESC LIMIT 1"), {"uid": uid})).mappings().one_or_none()
            goals = (await db.execute(text(
                "SELECT id, title, status FROM pa_goals WHERE user_id=:uid "
                "ORDER BY updated_at DESC"), {"uid": uid})).mappings().all()
            print(json.dumps({
                "prompts": [dict(row) for row in rows],
                "effective": {
                    "global_66666_count": global_prompt.count("66666"),
                    "global_666666_count": global_prompt.count("666666"),
                    "module_2_66666_count": module_prompt.count("66666"),
                    "module_2_666666_count": module_prompt.count("666666"),
                    "global_sha": hashlib.sha256(global_prompt.encode()).hexdigest()[:16],
                    "module_2_sha": hashlib.sha256(module_prompt.encode()).hexdigest()[:16],
                    "router_sha": hashlib.sha256(router.encode()).hexdigest()[:16],
                },
                "runtime": dict(runtime) if runtime else None,
                "goals": [dict(row) for row in goals],
            }, ensure_ascii=False, default=str))
    finally:
        await dispose_db()


asyncio.run(main())
