"""Idempotent additive migration: run before deploying the registration phone field."""
import asyncio
from app.db import get_engine
from app.models import AccountPhone


async def main():
    engine = get_engine()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: AccountPhone.__table__.create(sync, checkfirst=True))
        print("account_phones table ready")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
