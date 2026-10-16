"""DDL reflects existing FK encodings even under a legacy database default."""
from copy import deepcopy

import pytest
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import CreateTable

from app.database_v2_schema import metadata
from scripts.migrate_goal_cards_1003 import align_mysql_identifiers, private_table, reflected_identifier_type


class ColumnResult:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def one_or_none(self):
        return self.value


class ReflectedConnection:
    """The exact production reflection rows; no DDL is executed by alignment."""
    def __init__(self, columns):
        self.columns = columns
        self.queries = []

    async def execute(self, statement, params):
        sql = str(statement)
        assert sql.startswith("SELECT ") and "information_schema.COLUMNS" in sql
        assert "TABLE_SCHEMA = DATABASE()" in sql
        self.queries.append((sql, dict(params)))
        return ColumnResult(self.columns.get((params["table_name"], params["column_name"])))


def production_columns():
    return {(table, "uuid" if table == "user_profile" else "id"):
        {"DATA_TYPE": "varchar", "CHARACTER_MAXIMUM_LENGTH": 36,
         "CHARACTER_SET_NAME": "utf8mb4", "COLLATION_NAME": "utf8mb4_unicode_ci"}
        for table in ("user_profile", "pa_goals", "module_two_record", "pa_cycles")}


async def test_production_parent_collations_override_legacy_database_default():
    table = private_table()
    connection = ReflectedConnection(production_columns())
    report = await align_mysql_identifiers(connection, table)
    sql = str(CreateTable(table).compile(dialect=dialect()))
    for name in ("user_id", "goal_id", "plan_id", "cycle_id"):
        assert f"{name} VARCHAR(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" in sql
    assert "CHARSET=utf8mb4" in sql and "COLLATE utf8mb4_unicode_ci" in sql and "ENGINE=InnoDB" in sql
    assert len(report) == len(connection.queries) == 4
    assert all("ALTER" not in query for query, _ in connection.queries)
    # Reflection changes only the migration copy, not app metadata or parents.
    original = metadata.tables["goal_card_workspaces"]
    assert getattr(original.c.user_id.type, "charset", None) is None
    assert getattr(metadata.tables["user_profile"].c.uuid.type, "charset", None) is None


async def test_mixed_valid_parent_encodings_and_char_are_preserved_per_column():
    columns = production_columns()
    columns[("user_profile", "uuid")] = {"DATA_TYPE": "char", "CHARACTER_MAXIMUM_LENGTH": 36,
        "CHARACTER_SET_NAME": "ascii", "COLLATION_NAME": "ascii_bin"}
    before = deepcopy(columns)
    table = private_table()
    await align_mysql_identifiers(ReflectedConnection(columns), table)
    sql = str(CreateTable(table).compile(dialect=dialect()))
    assert "user_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin" in sql
    assert "goal_id VARCHAR(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" in sql
    assert columns == before


@pytest.mark.parametrize("change", [{"DATA_TYPE": "bigint"}, {"CHARACTER_MAXIMUM_LENGTH": 64},
    {"CHARACTER_SET_NAME": None}, {"COLLATION_NAME": "utf8mb4_unicode_ci; DROP TABLE x"}])
def test_unexpected_identifier_shape_fails_closed(change):
    description = {**production_columns()[("user_profile", "uuid")], **change}
    with pytest.raises(RuntimeError, match="Unexpected referenced identifier type"):
        reflected_identifier_type(private_table().c.user_id, description)


async def test_missing_parent_rejected_before_create():
    columns = production_columns(); del columns[("pa_cycles", "id")]
    with pytest.raises(RuntimeError, match="Required identifier column is missing"):
        await align_mysql_identifiers(ReflectedConnection(columns), private_table())
