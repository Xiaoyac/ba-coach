"""Read-only account and recent workflow inventory for a display nickname."""
import argparse, asyncio, json
from sqlalchemy import text
from app.db import get_sessionmaker, dispose_db

async def main(name):
    try:
        async with get_sessionmaker()() as db:
            rows = (await db.execute(text("""SELECT a.id,a.username,a.profile_uuid,p.nickname,h.base_username,h.tag
                FROM user_accounts a LEFT JOIN user_profile p ON p.uuid=a.profile_uuid
                LEFT JOIN account_handles h ON h.account_id=a.id
                WHERE LOWER(a.username)=:name OR LOWER(p.nickname)=:name
                   OR LOWER(h.normalized_base)=:name"""), {"name": name.lower()})).mappings().all()
            result=[]
            for row in rows:
                uid=row['profile_uuid']
                conversations=(await db.execute(text("""SELECT c.id,c.title,c.updated_at,r.current_module,r.active_goal_id,r.active_cycle_id,r.flow_status,r.last_transition_reason,COUNT(m.id) AS messages
                    FROM conversations c LEFT JOIN conversation_runtime_states r ON r.conversation_id=c.id
                    LEFT JOIN conversation_messages m ON m.conversation_id=c.id WHERE c.subject_id=:uid
                    GROUP BY c.id,c.title,c.updated_at,r.current_module,r.active_goal_id,r.active_cycle_id,r.flow_status,r.last_transition_reason
                    ORDER BY c.updated_at DESC LIMIT 10"""), {"uid":uid})).mappings().all()
                goals=(await db.execute(text("SELECT id,title,status,current_plan_record_id,updated_at FROM pa_goals WHERE user_id=:uid ORDER BY updated_at DESC"),{"uid":uid})).mappings().all()
                result.append({"account":dict(row),"conversations":[dict(x) for x in conversations],"goals":[dict(x) for x in goals]})
            print(json.dumps(result,ensure_ascii=False,default=str))
    finally:
        await dispose_db()

if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--name',required=True)
    args=parser.parse_args(); asyncio.run(main(args.name))
