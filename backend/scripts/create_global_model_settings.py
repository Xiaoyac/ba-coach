"""Idempotent additive migration: run before deploying the admin 部署 model button."""
import asyncio
from app.db import get_engine
from app.models import GlobalModelSetting


async def main():
    engine = get_engine()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: GlobalModelSetting.__table__.create(sync, checkfirst=True))
        print("global_model_settings table ready")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
