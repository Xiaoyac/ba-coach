"""Read-only by default. Add empty goal-model tables; never import user data.

PYTHONPATH=. python scripts/migrate_goal_details_0914.py --expected-database NAME
Add --apply only after inspecting the planned schema and authorizing DDL.
MySQL DDL auto-commits; partial runs are restartable, not transaction rollback.
"""
import argparse
import asyncio
from sqlalchemy import inspect, text, MetaData
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateTable, CreateIndex
from app.config import get_settings
from app.database_v2_schema import metadata

TABLE_NAMES = ("pa_goal_details", "pa_plan_details", "pa_activity_events", "pa_review_details")


async def align_mysql_foreign_keys(connection, table):
    """Match existing FK string encodings without altering any existing table."""
    for column in table.c:
        for foreign_key in column.foreign_keys:
            parent = foreign_key.column
            row = (await connection.execute(text(
                "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, CHARACTER_SET_NAME, COLLATION_NAME "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = :table AND COLUMN_NAME = :column"),
                {"table": parent.table.name, "column": parent.name})).mappings().one()
            if row["DATA_TYPE"] != "varchar" or row["CHARACTER_MAXIMUM_LENGTH"] != column.type.length:
                raise RuntimeError(f"Unexpected parent type for {table.name}.{column.name}")
            column.type = VARCHAR(column.type.length, charset=row["CHARACTER_SET_NAME"],
                                  collation=row["COLLATION_NAME"])
            print(f"FK ENCODING {table.name}.{column.name}: {row['CHARACTER_SET_NAME']} / {row['COLLATION_NAME']}")


async def migrate(url, *, expected_database, apply=False):
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        if engine.url.database != expected_database:
            raise RuntimeError("Database name does not match explicit expected target")
        if engine.dialect.name not in {"mysql", "sqlite"}:
            raise RuntimeError("Unsupported database dialect")
        async with engine.begin() as connection:
            # Use a private metadata copy: migration-specific reflected string
            # encodings must not mutate the running application's schema.
            target_metadata = MetaData()
            for source in metadata.sorted_tables:
                source.to_metadata(target_metadata)
            existing = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
            if not {"pa_goals", "module_two_record", "pa_cycles", "user_profile"}.issubset(existing):
                raise RuntimeError("Existing V2 schema is required; this script cannot create/convert it")
            for name in TABLE_NAMES:
                table = target_metadata.tables[name]
                if name in existing:
                    columns = await connection.run_sync(lambda c: {r["name"] for r in inspect(c).get_columns(name)})
                    if columns != set(table.c.keys()):
                        raise RuntimeError(f"Existing {name} differs from expected schema; manual review required")
                    print(f"EXISTS {name}")
                    continue
                if connection.dialect.name == "mysql":
                    await align_mysql_foreign_keys(connection, table)
                print(str(CreateTable(table).compile(dialect=connection.dialect)))
                for index in table.indexes:
                    print(str(CreateIndex(index).compile(dialect=connection.dialect)))
                if apply:
                    await connection.run_sync(lambda c: table.create(c, checkfirst=False))
                    print(f"CREATED {name}")
            print("Applied empty tables only" if apply else "READ ONLY: no DDL/data changes")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(migrate(get_settings().database_url, expected_database=args.expected_database, apply=args.apply))
