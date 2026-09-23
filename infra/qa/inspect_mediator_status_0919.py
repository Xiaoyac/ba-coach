"""Read effective public configuration and aggregate test-owned mediator events."""
import asyncio
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings


async def main():
    cfg = get_settings()
    print(json.dumps({"settings": {k: getattr(cfg, k, None) for k in (
        "knowledge_mediator_enabled", "knowledge_mediator_include_reasoning",
        "knowledge_mediator_reasoning_effort", "knowledge_mediator_timeout_seconds",
        "knowledge_mediator_max_tokens")}}, ensure_ascii=False))
    engine = create_async_engine(cfg.database_url)
    try:
        async with engine.connect() as db:
            rows = (await db.execute(text("""SELECT stage,model_name,duration_ms,finish_reason,error_code,event_metadata
                FROM ai_execution_events WHERE stage='knowledge_mediator' AND subject_id=(
                SELECT profile_uuid FROM user_accounts WHERE username=:u) ORDER BY id DESC LIMIT 12"""),
                {"u": "qaGoalb591262640"})).mappings().all()
            result = []
            for row in rows:
                meta = row["event_metadata"] or {}
                if isinstance(meta, str):
                    meta = json.loads(meta)
                result.append({k: row[k] for k in ("model_name", "duration_ms", "finish_reason", "error_code")} |
                              {k: meta.get(k) for k in ("status", "reason", "include_reasoning", "native_reasoning_present",
                               "raw_chunk_count", "approved_chunk_count", "withheld_on_error")})
            print(json.dumps({"test_account_recent_mediator": result}, ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
