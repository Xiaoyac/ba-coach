"""Read-only DMS diagnostic. Print schema names, never rows or credentials."""
import asyncio
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings


async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        if engine.dialect.name != "mysql" or engine.url.database != "ba_coach_260908":
            raise RuntimeError("Unexpected database target; no inspection performed")
        async with engine.connect() as connection:
            actual = await connection.scalar(text("SELECT DATABASE()"))
            schemas = list((await connection.execute(text(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME LIKE 'ba_coach%' ORDER BY SCHEMA_NAME"))).scalars())
            tables = list((await connection.execute(text(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME"))).scalars())
            checks = {}
            for name in ("user_profile", "pa_goals", "module_two_record", "pa_cycles", "pa_activity_events"):
                await connection.execute(text(f"SELECT * FROM `{name}` LIMIT 0"))
                checks[name] = "readable"
            print(json.dumps({"database": actual, "schema_version": settings.database_schema_version,
                "visible_ba_coach_databases": schemas, "tables": tables, "read_checks": checks}, ensure_ascii=False))
    finally:
        await engine.dispose()


asyncio.run(main())
