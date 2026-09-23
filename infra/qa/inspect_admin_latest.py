"""Read-only compact status for one account's latest conversation."""
import argparse
import asyncio
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings

async def main(conversation: int):
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as db:
            runtime = (await db.execute(text("""SELECT c.id,c.session_id,r.current_module,r.last_transition_reason,
                r.flow_status,r.active_goal_id,r.active_cycle_id,r.updated_at
                FROM conversations c LEFT JOIN conversation_runtime_states r ON r.conversation_id=c.id
                WHERE c.id=:id"""), {"id": conversation})).mappings().one_or_none()
            state = (await db.execute(text("""SELECT s.status,s.completed_steps,s.completion_source,s.evidence_status,s.updated_at
                FROM user_module_one_state s JOIN conversations c ON c.subject_id=s.user_id WHERE c.id=:id"""), {"id": conversation})).mappings().all()
            messages = (await db.execute(text("""SELECT id,position,role,CHAR_LENGTH(COALESCE(content,'')) AS chars,
                model_name,router_model_name,main_generation_duration_ms,router_duration_ms,created_at
                FROM conversation_messages WHERE conversation_id=:id ORDER BY position DESC,id DESC LIMIT 6"""), {"id": conversation})).mappings().all()
            events = (await db.execute(text("""SELECT id,assistant_message_id,stage,duration_ms,finish_reason,error_code,
                LEFT(event_metadata,1000) AS metadata,created_at FROM ai_execution_events
                WHERE session_id=(SELECT session_id FROM conversations WHERE id=:id)
                ORDER BY id DESC LIMIT 8"""), {"id": conversation})).mappings().all()
            print(json.dumps({"runtime":dict(runtime) if runtime else None,"m1_state":[dict(x) for x in state],
                "messages":[dict(x) for x in messages],"events":[dict(x) for x in events]},
                ensure_ascii=False,default=str))
    finally:
        await engine.dispose()

if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("--conversation",type=int,required=True)
    asyncio.run(main(p.parse_args().conversation))
