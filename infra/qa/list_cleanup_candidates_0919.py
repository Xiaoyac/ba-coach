"""Read-only account inventory for the explicitly requested test-user cleanup."""
import asyncio, json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings

async def main():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as db:
            rows = (await db.execute(text("""
                SELECT a.id, a.username, a.profile_uuid, p.nickname,
                       COUNT(DISTINCT c.id) AS conversations,
                       COUNT(DISTINCT m.id) AS messages,
                       a.created_at
                FROM user_accounts a
                LEFT JOIN user_profile p ON p.uuid=a.profile_uuid
                LEFT JOIN conversations c ON c.subject_id=a.profile_uuid
                LEFT JOIN conversation_messages m ON m.conversation_id=c.id
                WHERE p.nickname IN ('小林','线上验收','网页验收合成用户')
                   OR a.username LIKE 'e2e0919%'
                   OR a.username LIKE 'qaGoal%'
                GROUP BY a.id,a.username,a.profile_uuid,p.nickname,a.created_at
                ORDER BY a.created_at
            """))).mappings().all()
            print(json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str))
    finally: await engine.dispose()
asyncio.run(main())
