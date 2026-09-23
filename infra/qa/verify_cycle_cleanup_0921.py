import asyncio, json
from sqlalchemy import text
from app.db import get_sessionmaker, dispose_db
async def main():
    try:
        async with get_sessionmaker()() as db:
            rows=(await db.execute(text("SELECT id,username FROM user_accounts WHERE username LIKE 'qa0920%' OR username LIKE 'qaGoal%'"))).mappings().all()
            print(json.dumps({'remaining': [dict(row) for row in rows]}, ensure_ascii=False))
    finally: await dispose_db()
asyncio.run(main())
