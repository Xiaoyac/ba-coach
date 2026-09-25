"""Read-only by default: add explicit M3 recording decisions and evidence.

Legacy attitudes are never converted into acceptance/refusal. Existing rows get
unknown, and no recording plans or user records are rewritten. Apply only to an
explicitly named database after reviewing the printed additive migration plan.
"""
import argparse
import asyncio

from sqlalchemy import JSON, String, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


def migration_plan(columns):
    if not {"id", "acceptance_status", "negotiated_record_plan", "feedback_mechanism"} <= set(columns):
        raise RuntimeError("Expected existing V2 module_three_record")
    statements = []
    status = columns.get("recording_status")
    if status is None:
        statements.append("ALTER TABLE module_three_record ADD COLUMN recording_status "
            "VARCHAR(24) NOT NULL DEFAULT 'unknown' "
            "CHECK (recording_status IN ('unknown','accepted','declined'))")
    elif (not isinstance(status["type"], String) or status["type"].length != 24
          or status.get("nullable") is not False):
        raise RuntimeError("Existing recording_status differs from expected schema")
    evidence = columns.get("recording_evidence")
    if evidence is None:
        statements.append("ALTER TABLE module_three_record ADD COLUMN recording_evidence JSON NULL")
    elif not isinstance(evidence["type"], JSON) or evidence.get("nullable") is not True:
        raise RuntimeError("Existing recording_evidence differs from expected schema")
    return statements


async def migrate(url, *, expected_database, apply=False):
    engine = create_async_engine(url)
    try:
        if engine.url.database != expected_database:
            raise RuntimeError("Database name does not match explicit expected target")
        if engine.dialect.name not in {"mysql", "sqlite"}:
            raise RuntimeError("Unsupported database dialect")
        async with engine.begin() as connection:
            columns = await connection.run_sync(lambda c: {
                item["name"]:item for item in inspect(c).get_columns("module_three_record")})
            plan = migration_plan(columns)
            for statement in plan:
                print(statement)
                if apply:
                    await connection.execute(text(statement))
            print("APPLIED additive M3 columns; no consent inferred" if apply else "READ ONLY: no DDL/data changes")
            return plan
    finally:
        await engine.dispose()


if __name__ == "__main__":
    from app.config import get_settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(migrate(get_settings().database_url,
        expected_database=args.expected_database, apply=args.apply))
