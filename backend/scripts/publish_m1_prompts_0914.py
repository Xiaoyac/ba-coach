"""Publish ONLY the requested global/M1 prompt configuration, never user data.

Default is read-only inspection. Apply requires the exact inspected state hash
and a new private backup path. No local .db, spreadsheet or test data is read.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from sqlalchemy import select
from app.db import get_sessionmaker, dispose_db
from app.models import PromptOverride
from app.prompts import GLOBAL_PROMPT, MODULE_PROMPTS

KEYS = ("global", "module_1")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def main(args):
    async with get_sessionmaker()() as db:
        query = select(PromptOverride).where(PromptOverride.prompt_key.in_(KEYS)).order_by(PromptOverride.prompt_key)
        rows = list((await db.execute(query.with_for_update() if args.apply else query)).scalars())
        before = [{"key": r.prompt_key, "content": r.content, "updated_by": r.updated_by,
                   "updated_at": str(r.updated_at)} for r in rows]
        state_hash = digest(before)
        defaults = {"global": GLOBAL_PROMPT, "module_1": MODULE_PROMPTS["module_1"]}
        print(json.dumps({"state_hash": state_hash, "overrides": [
            {"key": r.prompt_key, "content_hash": digest(r.content), "chars": len(r.content)} for r in rows],
            "defaults": {k: {"hash": digest(v), "chars": len(v)} for k, v in defaults.items()}}, ensure_ascii=False))
        if not args.apply:
            return
        if not args.expected_state or args.expected_state != state_hash or not args.backup:
            raise RuntimeError("Prompt state changed or expected-state/backup missing; inspect again")
        backup = Path(args.backup)
        # Exclusive creation prevents accidental destruction of rollback evidence.
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": "m1-20260914-v1", "before": before,
                       "published_hashes": {k: digest(v) for k, v in defaults.items()}}, handle, ensure_ascii=False, indent=2)
        by_key = {r.prompt_key: r for r in rows}
        for key, value in defaults.items():
            row = by_key.get(key)
            if row is None:
                row = PromptOverride(prompt_key=key, content=value, updated_by="deployment:m1-20260914")
                db.add(row)
            else:
                row.content = value
                row.updated_by = "deployment:m1-20260914"
        await db.commit()
        print(json.dumps({"applied": list(KEYS), "backup": str(backup), "user_data_changed": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-state")
    parser.add_argument("--backup")
    args = parser.parse_args()
    async def run():
        try:
            await main(args)
        finally:
            await dispose_db()
    asyncio.run(run())
