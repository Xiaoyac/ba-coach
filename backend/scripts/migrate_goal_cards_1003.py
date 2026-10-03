"""Create only the empty M2 formulation workspace table. Dry-run by default.

MySQL databases can retain a legacy default charset even when their existing
V2 identifiers are utf8mb4. Reflect each referenced identifier into a private
DDL copy; never convert existing tables or mutate application metadata.
"""
import argparse
import asyncio
import json
import re

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.dialects.mysql import CHAR, VARCHAR
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from app.config import get_settings
from app.database_v2_schema import metadata


def private_table():
    private = MetaData()
    for source in metadata.sorted_tables:
        source.to_metadata(private)
    return private.tables["goal_card_workspaces"]


def reflected_identifier_type(column, description):
    """Only the known string ID shape may be adapted; fail on schema drift."""
    kind = str(description.get("DATA_TYPE", "")).lower()
    length = description.get("CHARACTER_MAXIMUM_LENGTH")
    charset, collation = description.get("CHARACTER_SET_NAME"), description.get("COLLATION_NAME")
    if (kind not in {"char", "varchar"} or length != column.type.length
            or not isinstance(charset, str) or not re.fullmatch(r"[A-Za-z0-9_]+", charset)
            or not isinstance(collation, str) or not re.fullmatch(r"[A-Za-z0-9_]+", collation)):
        raise RuntimeError(f"Unexpected referenced identifier type for {column.table.name}.{column.name}")
    return (CHAR if kind == "char" else VARCHAR)(length, charset=charset, collation=collation)


async def mysql_column(connection, table_name, column_name):
    row = (await connection.execute(text(
        "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, CHARACTER_SET_NAME, COLLATION_NAME "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = :table_name AND COLUMN_NAME = :column_name"),
        {"table_name": table_name, "column_name": column_name})).mappings().one_or_none()
    if row is None:
        raise RuntimeError(f"Required identifier column is missing: {table_name}.{column_name}")
    return dict(row)


async def align_mysql_identifiers(connection, table):
    encodings = []
    for column in table.columns:
        for foreign_key in column.foreign_keys:
            parent = foreign_key.column
            description = await mysql_column(connection, parent.table.name, parent.name)
            column.type = reflected_identifier_type(column, description)
            encodings.append({"column": column.name, "references": f"{parent.table.name}.{parent.name}",
                "type": description["DATA_TYPE"], "length": description["CHARACTER_MAXIMUM_LENGTH"],
                "charset": description["CHARACTER_SET_NAME"], "collation": description["COLLATION_NAME"]})
    # Textual draft/display content can include emoji. Do not inherit the old
    # database's utf8mb3 default; every FK retains its own reflected encoding.
    table.dialect_options["mysql"].update(charset="utf8mb4", collate="utf8mb4_unicode_ci", engine="InnoDB")
    return encodings


async def verify_existing(connection, table):
    reflected = await connection.run_sync(lambda c: inspect(c).get_columns(table.name))
    columns = {column["name"]: column for column in reflected}
    if set(columns) != set(table.c.keys()) or any(
            bool(columns[column.name]["nullable"]) != column.nullable for column in table.columns):
        raise RuntimeError("Existing goal-card table has an unexpected shape; no changes applied")
    actual_keys = await connection.run_sync(lambda c: inspect(c).get_foreign_keys(table.name))
    actual_refs = {(tuple(key["constrained_columns"]), key["referred_table"], tuple(key["referred_columns"])) for key in actual_keys}
    expected_refs = {((fk.parent.name,), fk.column.table.name, (fk.column.name,)) for fk in table.foreign_keys}
    if actual_refs != expected_refs:
        raise RuntimeError("Existing goal-card foreign keys differ; no changes applied")
    indexes = await connection.run_sync(lambda c: inspect(c).get_indexes(table.name))
    if not any(index["column_names"] == ["user_id", "conversation_id", "updated_at"] for index in indexes):
        raise RuntimeError("Goal-card owner/conversation index is missing; no changes applied")
    if connection.dialect.name == "mysql":
        for column in table.columns:
            if column.foreign_keys:
                actual = await mysql_column(connection, table.name, column.name)
                kind = "char" if isinstance(column.type, CHAR) else "varchar"
                if (actual["DATA_TYPE"].lower(), actual["CHARACTER_MAXIMUM_LENGTH"], actual["CHARACTER_SET_NAME"], actual["COLLATION_NAME"]) != (
                        kind, column.type.length, column.type.charset, column.type.collation):
                    raise RuntimeError(f"Existing goal-card identifier encoding differs: {column.name}")
        options = await connection.run_sync(lambda c: inspect(c).get_table_options(table.name))
        if str(options.get("mysql_engine", "")).lower() != "innodb":
            raise RuntimeError("Goal-card storage engine must enforce foreign keys")


async def migrate(database_url, expected_database, apply=False):
    engine = create_async_engine(database_url)
    table = private_table()
    try:
        if engine.url.database != expected_database:
            raise ValueError("Configured database does not match --expected-database")
        if engine.dialect.name not in {"mysql", "sqlite"}:
            raise ValueError("Unsupported database dialect")
        async with engine.begin() as connection:
            encodings = await align_mysql_identifiers(connection, table) if connection.dialect.name == "mysql" else []
            exists = await connection.run_sync(lambda c: inspect(c).has_table(table.name))
            if exists:
                await verify_existing(connection, table)
            print(json.dumps({"database": expected_database, "table": table.name, "apply": apply,
                "status": "already_current" if exists else "create_planned", "foreign_key_encodings": encodings}))
            if not exists:
                print(str(CreateTable(table).compile(dialect=connection.dialect)))
                for index in table.indexes:
                    print(str(CreateIndex(index).compile(dialect=connection.dialect)))
            if apply and not exists:
                await connection.run_sync(lambda c: table.create(c, checkfirst=False))
                await verify_existing(connection, table)
                print("Created empty goal-card workspaces; existing plans and conversations unchanged.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(migrate(get_settings().database_url, args.expected_database, args.apply))
