"""No live connections: migration plans must be scoped and preserve old scores."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import Integer, String

spec = importlib.util.spec_from_file_location('daily_migration', Path(__file__).parents[1] / 'scripts/migrate_daily_record_0917.py')
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def columns():
    entry = {name: {} for name in ['subject_id', 'recorded_on', 'completion_rate', 'overall_mood']}
    activity = {name: {} for name in ['emotion', 'activity', 'time_slot']}
    activity.update({name: {'type': Integer(), 'nullable': False} for name in ['achievement', 'connection', 'enjoyment', 'importance']})
    return entry, activity


def test_plan_only_adds_version_and_relaxes_optional_fields():
    entry, activity = columns()
    plan = script.migration_plan(entry, activity)
    assert len(plan) == 2
    assert 'DEFAULT 1, ALGORITHM=INSTANT' in plan[0]
    assert 'ALGORITHM=INPLACE, LOCK=NONE' in plan[1]
    assert all(word not in ' '.join(plan) for word in ['UPDATE ', 'DELETE ', 'DROP ', 'INSERT '])
    assert 'emotion' not in plan[1]


def test_plan_is_idempotent():
    entry, activity = columns()
    entry['scale_version'] = {'type': Integer(), 'nullable': False}
    for value in activity.values():
        value['nullable'] = True
    assert script.migration_plan(entry, activity) == []


@pytest.mark.parametrize('invalid', ['missing', 'type', 'nullable_version'])
def test_unexpected_schema_is_rejected(invalid):
    entry, activity = columns()
    if invalid == 'missing':
        del activity['emotion']
    elif invalid == 'type':
        activity['importance']['type'] = String()
    else:
        entry['scale_version'] = {'type': Integer(), 'nullable': True}
    with pytest.raises(RuntimeError):
        script.migration_plan(entry, activity)
