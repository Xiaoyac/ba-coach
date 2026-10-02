"""Independent in-chat PA reminder worker. No browser subscription or VAPID key."""
import argparse
import asyncio
import json

from sqlalchemy import select
from app.chat_reminder_schema import reminders
from app.db import dispose_db, get_sessionmaker
from app.pa_chat_reminders import available, tick


async def main(*, once=False, check=False):
    if not available():
        raise SystemExit('PA chat reminders disabled or database is not V2')
    try:
        # Fail before scanning if the explicit migration has not been applied.
        async with get_sessionmaker()() as db:
            await db.execute(select(reminders).limit(0))
        if check:
            print('READY: chat reminder schema verified; no messages sent')
            return
        while True:
            try:
                counts = await tick(get_sessionmaker())
                if once or any(counts[k] for k in ('sent', 'suppressed', 'failed')):
                    print(json.dumps(counts), flush=True)
                if once:
                    if counts['failed']:
                        raise SystemExit(1)
                    return
            except Exception as exc:
                print(json.dumps({'chat_reminder_worker_error': type(exc).__name__}), flush=True)
                if once:
                    raise SystemExit(1)
            await asyncio.sleep(30)
    finally:
        await dispose_db()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Read-only schema preflight')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(once=args.once, check=args.check))
