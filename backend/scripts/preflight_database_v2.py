"""Read-only production schema/aggregate inspection. Never emits user content or credentials.

Can run via SSH stdin from the backend directory; no files are written remotely.
"""
import asyncio
import argparse
import json
import re
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, ".")
from app.config import Settings


async def main(env_file, target_database):
    engine = create_async_engine(Settings(_env_file=env_file).database_url)
    try:
        if engine.url.get_backend_name() != "mysql":
            raise RuntimeError("Expected the scoped MySQL database")
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            tables = (await conn.execute(text(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME"
            ))).scalars().all()
            columns = (await conn.execute(text(
                "SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                "ORDER BY TABLE_NAME,ORDINAL_POSITION"
            ))).mappings().all()
            counts = {}
            for table in tables:
                if not re.fullmatch(r"[a-zA-Z0-9_]+", table):
                    raise ValueError("Unexpected table identifier")
                counts[table] = (await conn.execute(text(f"SELECT COUNT(*) FROM `{table}`"))).scalar_one()
            target_exists = (await conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=:name"
            ), {"name": target_database})).scalar_one()
            objects = {}
            for name, catalog, field in (("views", "VIEWS", "TABLE_SCHEMA"),
                    ("triggers", "TRIGGERS", "TRIGGER_SCHEMA"),
                    ("routines", "ROUTINES", "ROUTINE_SCHEMA"), ("events", "EVENTS", "EVENT_SCHEMA")):
                objects[name] = (await conn.execute(text(
                    f"SELECT COUNT(*) FROM information_schema.{catalog} WHERE {field}=DATABASE()"
                ))).scalar_one()
            print(json.dumps({"host": engine.url.host, "database": engine.url.database,
                              "target_database": target_database, "target_exists": bool(target_exists),
                              "other_objects": objects, "tables": counts,
                              "columns": [dict(c) for c in columns]}, default=str))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--env-file", default=".env")
        parser.add_argument("--target-database", default="ba_coach_260908")
        args = parser.parse_args()
        asyncio.run(main(args.env_file, args.target_database))
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "details": "redacted"}))
        sys.exit(1)
