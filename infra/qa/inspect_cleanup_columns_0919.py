import asyncio, json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings

async def main():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as db:
            rows = (await db.execute(text("""
                SELECT TABLE_NAME,COLUMN_NAME,DATA_TYPE
                FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
                AND COLUMN_NAME IN ('user_id','subject_id','profile_uuid','account_id','owner_id','created_by','updated_by')
                ORDER BY TABLE_NAME,COLUMN_NAME
            """))).mappings().all()
            print(json.dumps([dict(r) for r in rows], ensure_ascii=False))
            print((await db.execute(text('SHOW CREATE TABLE user_accounts'))).first()[1])
    finally: await engine.dispose()
asyncio.run(main())
