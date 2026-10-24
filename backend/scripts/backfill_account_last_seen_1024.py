"""Fill user_accounts.last_seen_at from activity that predates the column.

Only accounts with no last_seen_at yet are touched. The value is the latest of
the account's 每日测量 save, its own chat messages and its 每日记录 save.
Dry run by default; pass --apply to write.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.db import get_engine

LATEST = text("""
    SELECT a.id, a.username,
           GREATEST(
               COALESCE((SELECT MAX(m.updated_at) FROM daily_measurements m
                         WHERE m.subject_id = a.profile_uuid), CAST('1970-01-01' AS DATETIME)),
               COALESCE((SELECT MAX(cm.created_at) FROM conversation_messages cm
                         JOIN conversations c ON c.id = cm.conversation_id
                         WHERE c.subject_id = a.profile_uuid AND cm.role = 'user'), CAST('1970-01-01' AS DATETIME)),
               COALESCE((SELECT MAX(e.updated_at) FROM assessment_entries e
                         WHERE e.subject_id = a.profile_uuid), CAST('1970-01-01' AS DATETIME))
           ) AS latest
    FROM user_accounts a
    WHERE a.last_seen_at IS NULL
""")


async def main(apply: bool) -> None:
    engine = get_engine()
    try:
        async with engine.begin() as connection:
            if connection.dialect.name != "mysql":
                raise RuntimeError("This backfill is only for the deployed MySQL database")
            rows = [r for r in (await connection.execute(LATEST)).all() if r.latest.year > 1970]
            for row in rows:
                print(f"{row.username}: {row.latest} UTC")
            if apply:
                for row in rows:
                    # Re-check NULL so a request that landed meanwhile wins.
                    await connection.execute(text(
                        "UPDATE user_accounts SET last_seen_at = :seen "
                        "WHERE id = :id AND last_seen_at IS NULL"), {"seen": row.latest, "id": row.id})
            print(f"{len(rows)} accounts {'updated' if apply else 'would be updated (dry run)'}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
