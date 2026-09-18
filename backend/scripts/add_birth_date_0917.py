"""Add one nullable DATE column, without imports, backfills or account updates."""
import argparse
import asyncio
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings


async def migrate(url, *, expected_database, apply=False):
    engine = create_async_engine(url)
    try:
        if engine.url.database != expected_database:
            raise RuntimeError("Unexpected database; no changes made")
        if engine.dialect.name not in ("mysql", "sqlite"):
            raise RuntimeError("Unsupported database")
        async with engine.begin() as conn:
            columns = await conn.run_sync(lambda c: {x["name"]: x for x in inspect(c).get_columns("user_profile")})
            if engine.dialect.name == "mysql" and not {"uuid", "birth_year", "reported_age"}.issubset(columns):
                raise RuntimeError("Expected an existing V2 profile table")
            if "birth_date" in columns:
                if str(columns["birth_date"]["type"]).upper() != "DATE" or not columns["birth_date"]["nullable"]:
                    raise RuntimeError("Existing birth_date column is not nullable DATE")
                print("EXISTS user_profile.birth_date DATE NULL; no changes")
                return
            ddl = "ALTER TABLE user_profile ADD COLUMN birth_date DATE NULL"
            if engine.dialect.name == "mysql":
                ddl += ", ALGORITHM=INSTANT"
            print("PLAN " + ddl)
            if apply:
                if engine.dialect.name == "mysql":
                    await conn.execute(text("SET SESSION lock_wait_timeout=5"))
                await conn.execute(text(ddl))
                print("ADDED birth_date only; existing rows remain NULL; no data import or backfill")
            else:
                print("READ ONLY; use --apply after checking the target")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if settings.database_schema_version != "v2":
        raise RuntimeError("This deployment migration requires V2")
    asyncio.run(migrate(settings.database_url, expected_database=args.expected_database, apply=args.apply))
