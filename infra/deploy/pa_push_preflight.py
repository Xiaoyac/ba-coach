"""Read-only production check. Prints only schema/config flags and aggregate counts.

Run using the release Python, PYTHONPATH=., and the same systemd EnvironmentFiles
as the backend. Never outputs credentials, endpoints, user records or key bytes.
"""
import asyncio
import json
import uuid
from types import SimpleNamespace

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database_v2_schema import metadata


async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        if engine.dialect.name != "mysql" or settings.database_schema_version != "v2":
            raise RuntimeError("Expected production MySQL V2")
        if settings.startup_db_maintenance:
            raise RuntimeError("Startup database maintenance must be disabled")
        async with engine.connect() as conn:
            for table in metadata.tables.values():
                await conn.execute(select(table).limit(0))
            names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
            counts = {}
            for name in ("pa_push_devices", "pa_push_deliveries", "pa_push_checks"):
                counts[name] = (await conn.scalar(text(f"SELECT COUNT(*) FROM `{name}`"))) if name in names else None
            route_smoke = 'not_enabled'
            if getattr(settings, 'pa_push_enabled', False):
                from app.models import UserAccount
                from app.routes.push import status, history, recent_checks
                subject = str(uuid.uuid4())
                if await conn.scalar(select(UserAccount.id).where(UserAccount.profile_uuid == subject)):
                    raise RuntimeError('Probe identity collision')
                async with AsyncSession(bind=conn) as session:
                    caller = SimpleNamespace(subject_id=subject, session=SimpleNamespace(id=-1))
                    result = await status(caller=caller, db=session)
                    recent = await history(caller=caller, db=session)
                    diagnostic = await recent_checks(caller=caller, db=session)
                    if not result['available'] or result['devices'] or result['upcoming'] or recent['items'] or diagnostic['items']:
                        raise RuntimeError('Unexpected empty-account route result')
                    route_smoke = 'passed_read_only_no_account_created'
        print(json.dumps({
            "database": engine.url.database,
            "schema": settings.database_schema_version,
            "startup_maintenance": settings.startup_db_maintenance,
            "push_enabled": getattr(settings, "pa_push_enabled", False),
            "business_schema_read_check": "passed",
            "push_table_counts": counts,
            "push_query_smoke": route_smoke,
        }))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        raise SystemExit(f"Preflight failed: {type(exc).__name__}; details suppressed to protect production data") from None
