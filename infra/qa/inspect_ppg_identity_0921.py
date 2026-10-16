"""Read-only lookup of the reported display name, without credentials."""
import asyncio
import json
from sqlalchemy import text
from app.db import get_sessionmaker, dispose_db

async def main():
    try:
        async with get_sessionmaker()() as db:
            rows = (await db.execute(text("""SELECT a.id,a.username,p.nickname
                FROM user_accounts a LEFT JOIN user_profile p ON p.uuid=a.profile_uuid
                WHERE LOWER(a.username)=:name OR LOWER(p.nickname)=:name"""), {"name": "ppg"})).mappings().all()
            print(json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str))
    finally:
        await dispose_db()

asyncio.run(main())
