"""Duration reconciliation against the same row type returned by the database."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import RowMapping

from app.v2_workflow import _synchronize_duration_text


@pytest.fixture
def saved_plan():
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT :duration AS duration_minutes, :schedule AS schedule_text"
            ), {"duration": 5, "schedule": "每天晚上在书房做五分钟，先试三天"}).mappings().one()
        assert isinstance(row, RowMapping)
        return row
    finally:
        engine.dispose()


def test_duration_only_edit_updates_database_schedule(saved_plan):
    result = _synchronize_duration_text({"duration_minutes": 3}, saved_plan)

    assert result == {
        "duration_minutes": 3,
        "schedule_text": "每天晚上在书房做3分钟，先试三天",
    }
    assert saved_plan["duration_minutes"] == 5
    assert saved_plan["schedule_text"] == "每天晚上在书房做五分钟，先试三天"


def test_explicit_consistent_new_schedule_is_preserved(saved_plan):
    result = _synchronize_duration_text({
        "duration_minutes": 3, "schedule_text": "每天晚饭后做三分钟，先试两天",
    }, saved_plan)

    assert result == {
        "duration_minutes": 3,
        "schedule_text": "每天晚饭后做三分钟，先试两天",
    }


@pytest.mark.parametrize("schedule", [
    "五分钟热身后再做五分钟，先试三天",
    "先热身五分钟，再活动三分钟",
    "每天做四分钟，先试三天",
])
def test_ambiguous_edit_restores_both_saved_duration_and_text(saved_plan, schedule):
    result = _synchronize_duration_text({
        "duration_minutes": 3, "schedule_text": schedule, "location": "客厅",
    }, saved_plan)

    assert result == {
        "duration_minutes": saved_plan["duration_minutes"],
        "schedule_text": saved_plan["schedule_text"],
        "location": "客厅",
    }


def test_ambiguous_saved_schedule_keeps_its_pair():
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            saved = connection.execute(text(
                "SELECT 5 AS duration_minutes, :schedule AS schedule_text"
            ), {"schedule": "五分钟热身后再做五分钟，先试三天"}).mappings().one()

        result = _synchronize_duration_text({"duration_minutes": 3}, saved)

        assert result == dict(saved)
    finally:
        engine.dispose()
