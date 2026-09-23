"""Read-only diagnostics restricted to this run's generated QA account."""
import asyncio
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings


async def main():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as db:
            # All queries below are SELECTs scoped via the generated account.
            uid = (await db.execute(text("SELECT profile_uuid FROM user_accounts WHERE username=:u"),
                                    {"u": "qaGoalb591262640"})).scalar_one()
            logs = (await db.execute(text("""SELECT c.session_id,l.module_name,l.decision_type,
                l.decision_value,l.turn_id FROM ai_decision_logs l JOIN conversations c
                ON c.id=l.conversation_id WHERE c.subject_id=:uid ORDER BY l.id"""), {"uid": uid})).mappings().all()
            events = (await db.execute(text("""SELECT session_id,assistant_message_id,stage,duration_ms,
                provider,model_name,finish_reason,error_code FROM ai_execution_events
                WHERE subject_id=:uid ORDER BY id"""), {"uid": uid})).mappings().all()
            print(json.dumps({"decisions": [dict(x) for x in logs], "events": [dict(x) for x in events]},
                             ensure_ascii=False, default=str))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
