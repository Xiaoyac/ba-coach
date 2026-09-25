"""Idempotent M2 field alignment; read-only unless --apply is explicitly set.

Adds optional difficulty fields and removes the legacy universal long-term
direction CHECK. Does not rewrite goals, plan versions, confirmations or scores.
MySQL DDL auto-commits; rerunning after a partial application is supported.
"""
import argparse
import asyncio

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateColumn

from app.database_v2_schema import metadata


DIFFICULTY_COLUMNS = ("difficulty_rating", "difficulty_original", "difficulty_evidence")


def _requires_direction(check):
    sql = check.get("sqltext", "").lower().replace("`", "").replace('"', '')
    return "goal_kind" in sql and "primary" in sql and "long_term_direction is not null" in sql


async def migrate(url, *, expected_database, apply=False):
    engine = create_async_engine(url)
    try:
        if engine.url.database != expected_database:
            raise RuntimeError("Database name does not match explicit expected target")
        if engine.dialect.name not in {"mysql", "sqlite"}:
            raise RuntimeError("Unsupported database dialect")
        async with engine.begin() as connection:
            tables = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
            if not {"module_two_record", "pa_goal_details", "pa_goals"}.issubset(tables):
                raise RuntimeError("Existing V2 goal schema is required")
            columns = await connection.run_sync(lambda c: {v["name"] for v in inspect(c).get_columns("module_two_record")})
            for name in DIFFICULTY_COLUMNS:
                if name in columns:
                    print(f"EXISTS module_two_record.{name}")
                    continue
                column = metadata.tables["module_two_record"].c[name]
                definition = str(CreateColumn(column).compile(dialect=connection.dialect))
                if name == "difficulty_rating":
                    definition += " CHECK (difficulty_rating IS NULL OR difficulty_rating BETWEEN 0 AND 10)"
                elif name == "difficulty_original":
                    definition += " CHECK (difficulty_original IS NULL OR difficulty_original BETWEEN 6 AND 10)"
                ddl = f"ALTER TABLE module_two_record ADD COLUMN {definition}"
                print(ddl)
                if apply:
                    await connection.execute(text(ddl))
            checks = await connection.run_sync(lambda c: inspect(c).get_check_constraints("pa_goal_details"))
            direction_checks = [check for check in checks if _requires_direction(check)]
            if direction_checks and connection.dialect.name == "mysql":
                quote = connection.dialect.identifier_preparer.quote
                for check in direction_checks:
                    if not check.get("name"):
                        raise RuntimeError("Cannot remove an unnamed MySQL CHECK safely")
                    ddl = "ALTER TABLE pa_goal_details DROP CHECK " + quote(check["name"])
                    print(ddl)
                    if apply:
                        await connection.execute(text(ddl))
            elif direction_checks:
                # SQLite cannot DROP CHECK. Rebuild only this child metadata
                # table, copying every existing column and row without edits.
                current_columns = await connection.run_sync(lambda c: {v["name"] for v in inspect(c).get_columns("pa_goal_details")})
                expected_columns = set(metadata.tables["pa_goal_details"].c.keys())
                if current_columns != expected_columns:
                    raise RuntimeError("Unexpected pa_goal_details columns; refusing a lossy rebuild")
                indexes = await connection.run_sync(lambda c: inspect(c).get_indexes("pa_goal_details"))
                if indexes:
                    raise RuntimeError("Custom pa_goal_details indexes require explicit migration review")
                print("REBUILD pa_goal_details without the universal long-term direction CHECK; preserve all rows")
                if apply:
                    private = MetaData()
                    for table in metadata.sorted_tables:
                        table.to_metadata(private)
                    replacement = metadata.tables["pa_goal_details"].to_metadata(private, name="pa_goal_details_0924_tmp")
                    await connection.run_sync(lambda c: replacement.create(c))
                    names = ", ".join(connection.dialect.identifier_preparer.quote(name) for name in replacement.c.keys())
                    await connection.execute(text(f"INSERT INTO pa_goal_details_0924_tmp ({names}) SELECT {names} FROM pa_goal_details"))
                    await connection.execute(text("DROP TABLE pa_goal_details"))
                    await connection.execute(text("ALTER TABLE pa_goal_details_0924_tmp RENAME TO pa_goal_details"))
            else:
                print("ALIGNED pa_goal_details: no universal long-term direction CHECK")
            print("Applied M2 schema alignment" if apply else "READ ONLY: no schema or data changes")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    from app.config import get_settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Explicit local/test database URL; otherwise use configured URL")
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(migrate(args.database_url or get_settings().database_url,
                        expected_database=args.expected_database, apply=args.apply))
