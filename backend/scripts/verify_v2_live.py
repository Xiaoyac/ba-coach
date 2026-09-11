"""Read-only V2 deployment verification. Outputs aggregate counts only."""
import asyncio
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
settings = Settings(_env_file="/etc/bacoach/backend.env")
if settings.database_schema_version != "v2":
    raise RuntimeError("V2 is not enabled")
os.environ["DATABASE_URL"] = settings.database_url
os.environ["DATABASE_SCHEMA_VERSION"] = "v2"
from sqlalchemy import select, func
from app.db import get_sessionmaker, dispose_db
from app.database_v2_schema import metadata as schema
from app.models import UserAccount, Conversation
from app.v2_profile import read
from app.routes.auth import _account_info


async def main():
    async with get_sessionmaker()() as db:
        counts = {}
        for name, table in schema.tables.items():
            await db.execute(select(table).limit(0))
            counts[name] = (await db.execute(select(func.count()).select_from(table))).scalar_one()
        profile = schema.tables["user_profile"]
        users = (await db.execute(select(profile.c.uuid))).scalars().all()
        for user in users:
            await read(db, user)
        accounts = (await db.execute(select(UserAccount))).scalars().all()
        for account in accounts:
            await _account_info(db, account)
        rt = schema.tables["conversation_runtime_states"]
        missing = (await db.execute(select(func.count()).select_from(Conversation).outerjoin(
            rt, rt.c.conversation_id == Conversation.id).where(rt.c.conversation_id.is_(None)))).scalar_one()
        if missing:
            raise RuntimeError("Some conversations have no runtime")
    await dispose_db()
    print(json.dumps({"verified": True, "profiles_read": len(users), "accounts_read": len(accounts),
                      "missing_runtime": missing, "v2_rows": counts, "writes_performed": False}))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(json.dumps({"verified": False, "error_type": type(exc).__name__, "detail": "redacted"}))
        sys.exit(1)
