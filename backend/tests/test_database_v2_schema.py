"""Structural V2 contracts; these tests do not claim runtime cutover is ready."""
import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import mysql

from app.database_v2_schema import metadata


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.execute(insert(metadata.tables["user_profile"]), {"uuid": "user-a"})
        yield connection
    engine.dispose()


def test_schema_has_no_supporter_slots_and_compiles_for_mysql():
    assert not any(c.name.startswith("supporter") for c in metadata.tables["user_profile"].columns)
    assert len(metadata.tables) == 20
    for table in metadata.sorted_tables:
        assert "CREATE TABLE" in str(CreateTable(table).compile(dialect=mysql.dialect()))


def test_supporters_are_rows_not_fixed_slots(db):
    supporters = metadata.tables["user_supporters"]
    db.execute(insert(supporters), [
        {"id": f"supporter-{i}", "user_id": "user-a", "position": i,
         "relation": "自定义关系", "nickname": f"支持者{i}"} for i in range(12)
    ])
    assert len(db.execute(select(supporters)).all()) == 12


def test_multiple_goals_and_planning_cycle_without_confirmed_card(db):
    goals, cycles = metadata.tables["pa_goals"], metadata.tables["pa_cycles"]
    db.execute(insert(goals), [{"id": f"goal-{i}", "user_id": "user-a", "title": f"目标{i}"} for i in (1, 2)])
    db.execute(insert(cycles), {"id": "cycle-1", "goal_id": "goal-1", "ordinal": 1})
    assert db.execute(select(cycles.c.status)).scalar_one() == "planning"


def test_cycle_cannot_wait_for_execution_without_plan(db):
    db.execute(insert(metadata.tables["pa_goals"]), {"id": "g", "user_id": "user-a", "title": "目标"})
    with pytest.raises(IntegrityError):
        db.execute(insert(metadata.tables["pa_cycles"]), {
            "id": "c", "goal_id": "g", "ordinal": 1, "status": "waiting_execution"})


def test_m1_completion_requires_confirmed_formulation_pointer(db):
    with pytest.raises(IntegrityError):
        db.execute(insert(metadata.tables["user_module_one_state"]), {
            "user_id": "user-a", "completed_steps": [], "status": "completed"})


def test_legacy_completion_is_explicitly_imported_not_confirmed(db):
    states = metadata.tables["user_module_one_state"]
    db.execute(insert(states), {"user_id": "user-a", "completed_steps": [],
        "status": "completed", "completion_source": "legacy_imported", "evidence_status": "missing"})
    row = db.execute(select(states)).mappings().one()
    assert row["confirmed_formulation_id"] is None
    assert row["completed_steps"] == []


def test_legacy_completion_cannot_claim_confirmation_evidence(db):
    with pytest.raises(IntegrityError):
        db.execute(insert(metadata.tables["user_module_one_state"]), {
            "user_id": "user-a", "completed_steps": [], "status": "completed",
            "completion_source": "legacy_imported", "evidence_status": "available"})


def test_m1_confirmed_record_requires_confirmation_evidence(db):
    with pytest.raises(IntegrityError):
        db.execute(insert(metadata.tables["module_one_record"]), {
            "id": "m1", "user_id": "user-a", "version_no": 1,
            "record_status": "confirmed", "confirmation_status": "confirmed"})


def test_progress_is_cycle_scoped_not_conversation_scoped():
    progress = metadata.tables["pa_cycle_progress"]
    assert list(progress.primary_key.columns.keys()) == ["cycle_id"]
    assert "conversation_id" not in progress.c


def test_memory_has_independent_key_and_provenance():
    memory = metadata.tables["ba_memory"]
    assert {"memory_key", "source_kind", "source_message_id", "confirmation_status", "supersedes_id"} <= set(memory.c.keys())
