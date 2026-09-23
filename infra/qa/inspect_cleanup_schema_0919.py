import asyncio,json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings
async def main():
 e=create_async_engine(get_settings().database_url)
 try:
  async with e.connect() as db:
   tables=list((await db.execute(text("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME"))).scalars())
   for t in tables:
    cols=(await db.execute(text("SELECT COLUMN_NAME,DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t ORDER BY ORDINAL_POSITION"),{'t':t})).mappings().all()
    fks=(await db.execute(text("SELECT COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t AND REFERENCED_TABLE_NAME IS NOT NULL"),{'t':t})).mappings().all()
    print(json.dumps({'table':t,'columns':[dict(x) for x in cols],'fks':[dict(x) for x in fks]},ensure_ascii=False))
 finally: await e.dispose()
asyncio.run(main())
