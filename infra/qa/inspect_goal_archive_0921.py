"""Read-only admin goal diagnosis and exact cycle-QA account inventory."""
import asyncio, json
from sqlalchemy import text
from app.db import get_sessionmaker, dispose_db
from app.routes.program import get_goal_overview

async def main():
    try:
        async with get_sessionmaker()() as db:
            uid=await db.scalar(text("SELECT profile_uuid FROM user_accounts WHERE username='admin'"))
            p={'uid':uid}
            for label,sql in [
                ('runtime', '''SELECT c.id,c.session_id,c.title,c.updated_at,r.current_module,r.active_goal_id,r.active_cycle_id,r.flow_status,r.last_transition_reason,r.memory
                FROM conversations c LEFT JOIN conversation_runtime_states r ON r.conversation_id=c.id
                WHERE c.subject_id=:uid ORDER BY c.updated_at DESC LIMIT 5'''),
                ('goals', 'SELECT id,title,status,current_plan_record_id,created_from_conversation_id,updated_at FROM pa_goals WHERE user_id=:uid ORDER BY updated_at DESC'),
                ('messages', '''SELECT m.id,m.conversation_id,m.role,LEFT(m.content,2200) AS content FROM conversation_messages m
                JOIN conversations c ON c.id=m.conversation_id WHERE c.subject_id=:uid ORDER BY m.id DESC LIMIT 12'''),
                ('events', '''SELECT assistant_message_id,stage,error_code,event_metadata,created_at FROM ai_execution_events
                WHERE subject_id=:uid AND stage IN ('clinical_extraction','module_router') ORDER BY id DESC LIMIT 8'''),
                ('cycle_accounts', '''SELECT a.id,a.username,a.profile_uuid,p.nickname,s.role,a.created_at
                FROM user_accounts a JOIN user_profile p ON p.uuid=a.profile_uuid
                LEFT JOIN account_settings s ON s.account_id=a.id
                WHERE (a.username LIKE 'qa0920%' AND p.nickname LIKE '周期验收0920%')
                   OR (a.username LIKE 'qaGoal%' AND p.nickname='目标周期验收') ORDER BY a.id''')]:
                rows=(await db.execute(text(sql),p)).mappings().all()
                print(json.dumps({label:[dict(row) for row in rows]},ensure_ascii=False,default=str))
            print(json.dumps({'overview':await get_goal_overview(user_id=uid,db=db)},ensure_ascii=False,default=str))
    finally: await dispose_db()

asyncio.run(main())
