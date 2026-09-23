"""Read-only M2/M3 transition diagnosis for a display nickname."""
import argparse, asyncio, json
from sqlalchemy import text
from app.db import get_sessionmaker, dispose_db

async def main(name):
    try:
        async with get_sessionmaker()() as db:
            uid=await db.scalar(text("SELECT profile_uuid FROM user_accounts WHERE username=:name OR profile_uuid IN (SELECT uuid FROM user_profile WHERE nickname=:name)"),{"name":name})
            if not uid:
                uid=await db.scalar(text("SELECT uuid FROM user_profile WHERE nickname=:name"),{"name":name})
            conv=await db.scalar(text("SELECT id FROM conversations WHERE subject_id=:uid ORDER BY updated_at DESC LIMIT 1"),{"uid":uid})
            params={"uid":uid,"cid":conv}
            queries={
              "runtime":"SELECT c.id,c.session_id,r.current_module,r.active_goal_id,r.active_cycle_id,r.flow_status,r.last_transition_reason,r.memory FROM conversations c JOIN conversation_runtime_states r ON r.conversation_id=c.id WHERE c.id=:cid",
              "messages":"SELECT id,position,role,content,model_name,router_model_name,routing_reasoning_content,created_at FROM conversation_messages WHERE conversation_id=:cid ORDER BY position,id",
              "events":"SELECT assistant_message_id,stage,error_code,event_metadata,created_at FROM ai_execution_events WHERE session_id=(SELECT session_id FROM conversations WHERE id=:cid) ORDER BY id",
              "decisions":"SELECT turn_id,module_name,decision_type,decision_value,created_at FROM ai_decision_logs WHERE conversation_id=:cid ORDER BY created_at",
              "m2":"SELECT m.* FROM module_two_record m JOIN pa_goals g ON g.id=m.goal_id WHERE g.user_id=:uid ORDER BY m.updated_at DESC",
              "m3":"SELECT m.* FROM module_three_record m JOIN pa_goals g ON g.id=m.goal_id WHERE g.user_id=:uid ORDER BY m.updated_at DESC",
              "cycles":"SELECT c.* FROM pa_cycles c JOIN pa_goals g ON g.id=c.goal_id WHERE g.user_id=:uid ORDER BY c.created_at DESC",
            }
            out={"uid":uid,"conversation":conv}
            for key,sql in queries.items():
                out[key]=[dict(r) for r in (await db.execute(text(sql),params)).mappings().all()]
            print(json.dumps(out,ensure_ascii=False,default=str))
    finally: await dispose_db()

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--name',required=True); a=p.parse_args(); asyncio.run(main(a.name))
