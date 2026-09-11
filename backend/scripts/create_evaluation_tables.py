"""Explicit, additive migration for the three admin evaluation tables only.

Default is read-only. Never invokes application startup or business migrations.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.test_workbench import EvaluationCase, EvaluationReview, EvaluationRun

TABLES = [EvaluationCase.__table__, EvaluationRun.__table__, EvaluationReview.__table__]


async def migrate(engine, expected_database: str, apply: bool = False) -> dict:
    if engine.url.database != expected_database:
        raise ValueError("Database does not match --expected-database")
    async with engine.connect() as conn:
        existing = set(await conn.run_sync(lambda sync: inspect(sync).get_table_names()))
        if "user_accounts" not in existing:
            raise ValueError("Required user_accounts table is missing")
        # Do not silently accept an incompatible pre-existing table.
        for table in TABLES:
            if table.name in existing:
                columns = await conn.run_sync(lambda sync, name=table.name: inspect(sync).get_columns(name))
                if {c.name for c in table.columns} != {c["name"] for c in columns}:
                    raise ValueError("Existing evaluation table columns differ; manual inspection required")
        missing = [table.name for table in TABLES if table.name not in existing]
        if apply:
            for table in TABLES:
                await conn.run_sync(lambda sync, target=table: target.create(sync, checkfirst=True))
            await conn.commit()
        return {"database": expected_database, "apply": apply, "missing_before": missing,
                "scope": [t.name for t in TABLES], "business_tables_changed": False}


async def main(args):
    engine = create_async_engine(Settings(_env_file=args.env_file).database_url)
    try:
        print(json.dumps(await migrate(engine, args.expected_database, args.apply), ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--apply", action="store_true")
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        # Connection exception text can contain credentials; never print it.
        print(json.dumps({"error_type": type(exc).__name__, "details": "Migration aborted; inspect configuration/schema without exposing credentials"}))
        raise SystemExit(1) from None
