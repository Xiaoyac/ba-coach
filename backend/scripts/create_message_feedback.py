"""Idempotent additive migration: run before deploying reply feedback routes."""
import asyncio
from app.db import get_engine
from app.models import MessageFeedback


async def main():
    engine = get_engine()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: MessageFeedback.__table__.create(sync, checkfirst=True))
        print("message_feedback table ready")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
