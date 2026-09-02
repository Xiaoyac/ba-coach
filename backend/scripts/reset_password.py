"""Reset an account's password from the command line.

    python scripts/reset_password.py <username>

There is no self-service password reset in the app: the account schema has no
email or phone to send a link to (see `app.models.UserAccount` on why
credentials live in their own table at all), so recovery is deliberately an
operator action. This is that action.

The new password is read with `getpass`, so it is never echoed to the screen,
never becomes a shell argument, and never lands in shell history — which is
exactly why it is not accepted as a command-line flag. Stored hashed with
Argon2id, the same way registration does it.

Every existing session for the account is revoked as part of the reset. A
forgotten password is indistinguishable from a compromised one, and leaving
old bearer tokens alive would mean a reset that does not actually lock anyone
out.
"""

from __future__ import annotations

import asyncio
import getpass
import sys
from pathlib import Path

# Allow `python scripts/reset_password.py` from the backend directory without
# installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.account_identity import (  # noqa: E402
    find_account_by_username,
)
from app.db import dispose_db, get_sessionmaker, init_db  # noqa: E402
from app.models import AuthSession, UserAccount  # noqa: E402
from app.security import hash_password  # noqa: E402

# Matches RegisterRequest.password in app/schemas.py. Kept in sync by hand;
# a mismatch here would let this tool set a password the API would reject.
MIN_LENGTH = 8


def _prompt_for_password() -> str:
    """Read the new password twice, without echoing it."""
    while True:
        first = getpass.getpass("新密码（输入时不会显示）: ")
        if len(first) < MIN_LENGTH:
            print(f"  密码至少 {MIN_LENGTH} 位，请重试。", file=sys.stderr)
            continue
        second = getpass.getpass("再输入一次: ")
        if first != second:
            print("  两次输入不一致，请重试。", file=sys.stderr)
            continue
        return first


async def _reset(username: str) -> int:
    await init_db()
    async with get_sessionmaker()() as db:
        account = await find_account_by_username(db, username)

        if account is None:
            print(f"找不到登录账号 {username!r}。", file=sys.stderr)
            existing = (await db.execute(select(UserAccount.username))).scalars().all()
            if existing:
                print(f"现有账号: {', '.join(sorted(existing))}", file=sys.stderr)
            return 1

        print(f"正在为登录账号 {account.username!r} 重置密码。")
        password = _prompt_for_password()

        account.password_hash = hash_password(password)

        sessions = (
            await db.execute(
                select(AuthSession).where(AuthSession.account_id == account.id)
            )
        ).scalars().all()
        for session in sessions:
            await db.delete(session)

        await db.commit()

    print(
        f"完成。已重置密码，并注销了 {len(sessions)} 个登录会话"
        "（所有设备都需要重新登录）。"
    )
    return 0


async def reset(username: str) -> int:
    """Run the reset and close the connection pool in the same event loop.

    Disposing from a second `asyncio.run` — the obvious-looking way to do
    cleanup in `main`'s `finally` — tears the pool down against an already
    closed loop, which produces a page of `Event loop is closed` tracebacks
    after an otherwise successful run.
    """
    try:
        return await _reset(username)
    finally:
        await dispose_db()


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    try:
        return asyncio.run(reset(sys.argv[1]))
    except KeyboardInterrupt:
        print("\n已取消，密码未改动。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
