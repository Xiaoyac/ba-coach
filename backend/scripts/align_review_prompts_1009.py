"""Targeted administrator prompt migration; dry-run unless --apply.

Consumes a reviewed pack of exact before hashes and replacement content. It
does not substitute defaults for administrator prompts. Rollback is conditional
on the content still matching this migration, preserving later admin edits.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import select

from app.db import get_sessionmaker, dispose_db
from app.models import PromptOverride
from app.prompt_history_models import PromptHistoryState
from sqlalchemy import update

ALLOWED = {"module_2", "module_3", "module_4"}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


async def apply_pack(pack, *, apply=False, backup=None):
    if not pack or set(pack) - ALLOWED:
        raise ValueError("Only the reviewed M2/M3/M4 prompt updates are allowed")
    async with get_sessionmaker()() as db:
        async with db.begin():
            rows = {r.prompt_key: r for r in (await db.execute(select(PromptOverride)
                .where(PromptOverride.prompt_key.in_(pack)).with_for_update())).scalars()}
            old = {}
            report = []
            for key, change in pack.items():
                row = rows.get(key)
                if row is None or digest(row.content) != change["before_sha256"]:
                    raise ValueError(f"Effective prompt changed or missing: {key}")
                if not change["content"].strip():
                    raise ValueError(f"Empty replacement: {key}")
                old[key] = {"before_sha256": digest(change["content"]), "content": row.content,
                            "updated_by": row.updated_by}
                report.append({"key": key, "before": digest(row.content),
                               "after": digest(change["content"]), "applied": apply})
            if apply:
                if backup is None:
                    raise ValueError("--backup is required for --apply")
                # O_EXCL prevents replacing an earlier recovery point.
                fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as handle:
                    json.dump(old, handle, ensure_ascii=False, indent=2)
                from datetime import datetime, timezone
                for key, change in pack.items():
                    await db.execute(update(PromptHistoryState).where(PromptHistoryState.prompt_key == key).values(pending_actor="system:requirements-1009"))
                    rows[key].content = change["content"]
                    rows[key].updated_by = change.get("updated_by", "system:requirements-1009")
                    rows[key].updated_at = datetime.now(timezone.utc)
            return report


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    try:
        report = await apply_pack(json.loads(args.pack.read_text()), apply=args.apply, backup=args.backup)
        print(json.dumps(report, ensure_ascii=False))
    finally:
        await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
