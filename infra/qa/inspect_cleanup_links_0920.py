"""Read-only ownership diagnostics; never print message/log payloads."""
import asyncio
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings

async def main():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as db:
            for name in ('ai_decision_logs', 'conversations'):
                print((await db.execute(text('SHOW CREATE TABLE ' + name))).first()[1])
            rows = (await db.execute(text('''SELECT l.id,l.conversation_id,l.goal_id,l.cycle_id,
                g.user_id AS goal_owner,c.subject_id AS conversation_owner
                FROM ai_decision_logs l JOIN pa_goals g ON g.id=l.goal_id
                LEFT JOIN conversations c ON c.id=l.conversation_id
                WHERE g.user_id IN (SELECT profile_uuid FROM user_accounts WHERE id BETWEEN 36 AND 50)
                ORDER BY l.id'''))).mappings().all()
            print(json.dumps([dict(r) for r in rows], default=str))
    finally:
        await engine.dispose()

asyncio.run(main())
