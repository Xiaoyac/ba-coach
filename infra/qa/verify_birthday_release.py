"""Read-only release verification: schema and aggregate counts, never identities."""
import asyncio
import json
from urllib.request import urlopen
from sqlalchemy import inspect, select, func
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings
from app.database_v2_schema import metadata
from app.models import UserAccount


async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        if engine.url.database != "ba_coach_260908" or settings.database_schema_version != "v2":
            raise RuntimeError("Unexpected production target")
        async with engine.connect() as connection:
            for table in metadata.tables.values():
                await connection.execute(select(table).limit(0))
            columns = await connection.run_sync(lambda c: {x["name"]: x for x in inspect(c).get_columns("user_profile")})
            column = columns["birth_date"]
            assert str(column["type"]).upper() == "DATE" and column["nullable"]
            profile = metadata.tables["user_profile"]
            pending = await connection.scalar(select(func.count()).select_from(profile.join(
                UserAccount, UserAccount.profile_uuid == profile.c.uuid)).where(
                profile.c.reported_age == 10, profile.c.birth_date.is_(None)))
            with urlopen("http://127.0.0.1:8000/openapi.json", timeout=10) as response:
                spec = json.load(response)
            registration = spec["components"]["schemas"]["RegisterRequest"]
            assert "birth_date" in registration["required"]
            assert "birth_date_required" in spec["components"]["schemas"]["AccountInfo"]["properties"]
            assert "put" in spec["paths"]["/api/auth/birth-date"]
            print(json.dumps({"schema": "v2", "database": engine.url.database,
                "all_expected_columns_readable": True, "birthday_column": "DATE NULL",
                "startup_db_maintenance_effective": settings.startup_db_maintenance and settings.database_schema_version != "v2",
                "live_api_birthday_contract": "passed",
                "registered_accounts_requiring_birthday": pending}))
    finally:
        await engine.dispose()


asyncio.run(main())
