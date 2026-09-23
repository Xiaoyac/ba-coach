"""Read-only probe of the M1 consent invitation against the stored transcript."""
import asyncio, json, re
from sqlalchemy import text
from app.db import get_sessionmaker, dispose_db
from app.m1_contract import consent_is_current, is_goal_discussion_invitation

async def main():
    try:
        async with get_sessionmaker()() as db:
            rows = (await db.execute(text("""SELECT m.role,m.content FROM conversation_messages m
                WHERE m.conversation_id=212 AND m.position>=0 ORDER BY m.position,m.id"""))).all()
            turns = [(r[0], r[1]) for r in rows]
            hits=[]
            for i,(role,body) in enumerate(turns):
                if role == "user" and ("好的" in body or "可以" in body or "愿意" in body):
                    prev = turns[i-1][1] if i and turns[i-1][0] == "assistant" else ""
                    hits.append({"index":i,"body":body,"previous":prev,"previous_question_count":prev.count("？")+prev.count("?"),
                                 "outside_quote": re.sub(r'“[^”]*”|「[^」]*」|‘[^’]*’|"[^"\\n]*"', '', prev),
                                 "invitation":is_goal_discussion_invitation(prev),
                                 "consent":consent_is_current(turns,i)})
            print(json.dumps(hits, ensure_ascii=False))
    finally:
        await dispose_db()
asyncio.run(main())
