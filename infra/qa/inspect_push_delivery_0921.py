"""Read-only delivery diagnosis; never print endpoints, keys, or tokens."""
import asyncio
import json
from urllib.parse import urlsplit
from sqlalchemy import select
from app.db import get_sessionmaker, dispose_db
from app.models import UserAccount
from app.push_schema import checks, devices
from app.pa_push_checks import public_check


async def main():
    try:
        async with get_sessionmaker()() as db:
            uid = await db.scalar(select(UserAccount.profile_uuid).where(UserAccount.username == 'admin'))
            rows = (await db.execute(select(checks).where(checks.c.user_id == uid).order_by(checks.c.created_at.desc()).limit(10))).mappings().all()
            print(json.dumps({'checks': [public_check(row) for row in rows]}, default=str))
            rows = (await db.execute(select(devices).where(devices.c.user_id == uid))).mappings().all()
            print(json.dumps({'devices': [{'id': row['id'], 'provider': urlsplit(row['endpoint']).hostname,
                'enabled': row['enabled'], 'updated_at': str(row['updated_at'])} for row in rows]}))
    finally:
        await dispose_db()


asyncio.run(main())
