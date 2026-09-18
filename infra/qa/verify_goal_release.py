"""Read-only production goal-model checks; never print user content or secrets."""
import asyncio
import json
from sqlalchemy import inspect, select, func
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings
from app.database_v2_schema import metadata


async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        if engine.url.database != 'ba_coach_260908' or settings.database_schema_version != 'v2':
            raise RuntimeError('Unexpected production schema target')
        async with engine.connect() as connection:
            names = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
            missing = set(metadata.tables) - names
            if missing:
                raise RuntimeError(f'Missing V2 tables: {sorted(missing)}')
            rows = {}
            for name in ('pa_goal_details', 'pa_plan_details', 'pa_activity_events', 'pa_review_details'):
                table = metadata.tables[name]
                columns = await connection.run_sync(lambda c: {x['name'] for x in inspect(c).get_columns(name)})
                if columns != set(table.c.keys()):
                    raise RuntimeError(f'Column mismatch: {name}')
                rows[name] = (await connection.execute(select(func.count()).select_from(table))).scalar_one()
            print(json.dumps({'schema':'v2','all_expected_tables_present':True,
                'startup_db_maintenance':settings.startup_db_maintenance,'new_table_row_counts':rows}))
    finally:
        await engine.dispose()


asyncio.run(main())
