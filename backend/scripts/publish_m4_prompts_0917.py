"""Publish only module_4 configuration. Read-only unless --apply with state hash.

All M4 columns already exist in V2. This script never imports local databases,
creates users, changes clinical records, or imports a knowledge corpus.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from sqlalchemy import select, inspect
from app.db import get_sessionmaker, get_engine, dispose_db
from app.models import PromptOverride
from app.prompts import MODULE_PROMPTS
from app.database_v2_schema import metadata


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def run(args):
    engine = get_engine()
    if engine.url.database != args.database:
        raise RuntimeError("Database target mismatch")
    async with engine.connect() as conn:
        columns = await conn.run_sync(lambda c: [v["name"] for v in inspect(c).get_columns("module_four_record")])
        expected = set(metadata.tables["module_four_record"].c.keys())
        missing = sorted(expected - set(columns))
        if missing:
            raise RuntimeError("Required V2 columns absent: " + ",".join(missing))
    async with get_sessionmaker()() as db:
        query = select(PromptOverride).where(PromptOverride.prompt_key == "module_4")
        row = (await db.execute(query.with_for_update() if args.apply else query)).scalar_one_or_none()
        before = {"content": row.content, "updated_by": row.updated_by, "updated_at": str(row.updated_at)} if row else None
        state_hash = digest(before)
        content = MODULE_PROMPTS["module_4"]
        print(json.dumps({"database": args.database, "m4_columns_verified": len(expected), "ddl_required": False,
            "state_hash": state_hash, "before_content_hash": digest(row.content) if row else None,
            "new_content_hash": digest(content), "new_chars": len(content)}, ensure_ascii=False))
        if not args.apply:
            return
        if state_hash != args.expected_state or not args.backup:
            raise RuntimeError("State changed or expected-state/backup missing")
        fd = os.open(Path(args.backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": "m4-20260917-v1", "key": "module_4", "before": before,
                       "published_hash": digest(content)}, handle, ensure_ascii=False, indent=2)
        if row is None:
            db.add(PromptOverride(prompt_key="module_4", content=content, updated_by="deployment:m4-20260917"))
        else:
            row.content, row.updated_by = content, "deployment:m4-20260917"
        await db.commit()
        print(json.dumps({"applied": ["module_4"], "backup": args.backup, "clinical_data_changed": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-state")
    parser.add_argument("--backup")
    args = parser.parse_args()
    async def main():
        try:
            await run(args)
        finally:
            await dispose_db()
    asyncio.run(main())
