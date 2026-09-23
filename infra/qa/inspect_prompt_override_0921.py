"""Read-only production prompt override metadata and effective hashes."""
import asyncio, hashlib, json
from sqlalchemy import text
from app.db import get_sessionmaker, dispose_db
from app.prompt_store import effective_prompt_pair, effective_router_prompt

async def main():
    try:
        async with get_sessionmaker()() as db:
            rows = (await db.execute(text("SELECT prompt_key, LENGTH(content) AS chars, updated_by, updated_at, LEFT(content, 180) AS preview FROM prompt_overrides ORDER BY prompt_key"))).mappings().all()
            global_prompt, module_prompt = await effective_prompt_pair(db, 'module_2')
            router = await effective_router_prompt(db)
            print(json.dumps({'overrides':[dict(r) for r in rows], 'effective': {
                'module_2_contains_666666': '666666' in module_prompt,
                'global_contains_666666': '666666' in global_prompt,
                'router_contains_666666': '666666' in router,
                'module_2_sha': hashlib.sha256(module_prompt.encode()).hexdigest()[:16],
                'global_sha': hashlib.sha256(global_prompt.encode()).hexdigest()[:16],
                'router_sha': hashlib.sha256(router.encode()).hexdigest()[:16],
                'global_66666_count': global_prompt.count('66666'),
                'global_666666_count': global_prompt.count('666666'),
                'module_2_66666_count': module_prompt.count('66666'),
                'module_2_666666_count': module_prompt.count('666666'),
            }}, ensure_ascii=False, default=str))
    finally:
        await dispose_db()

asyncio.run(main())
