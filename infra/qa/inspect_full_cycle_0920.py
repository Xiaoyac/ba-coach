"""Read-only production diagnostics, restricted to this run's synthetic users."""
import asyncio
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings

async def main():
    e=create_async_engine(get_settings().database_url)
    try:
        async with e.connect() as db:
            users=tuple((await db.execute(text("SELECT a.profile_uuid FROM user_accounts a JOIN user_profile p ON p.uuid=a.profile_uuid WHERE a.username LIKE 'qa0920%' AND p.nickname LIKE '周期验收0920%'"))).scalars())
            assert users
            params={'users':users}
            queries={
                'events':'''SELECT session_id,assistant_message_id,stage,duration_ms,provider,model_name,
                    finish_reason,error_code,event_metadata FROM ai_execution_events WHERE subject_id IN :users ORDER BY id''',
                'decisions':'''SELECT c.session_id,l.module_name,l.decision_type,l.decision_value,l.turn_id
                    FROM ai_decision_logs l JOIN conversations c ON c.id=l.conversation_id
                    WHERE c.subject_id IN :users ORDER BY l.id''',
            }
            result={k:[dict(r) for r in (await db.execute(text(sql),params)).mappings()] for k,sql in queries.items()}
            print(json.dumps(result,ensure_ascii=False,default=str))
    finally:
        await e.dispose()

asyncio.run(main())
