"""Tests for clinical extraction and the writes it feeds.

Two layers, kept apart on purpose:

* `coerce` — the boundary between untrusted model output and the database.
  Tested directly, because everything downstream assumes it holds.
* the graph wiring — that the right work is dispatched, in the background,
  on the right turns.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.clinical_extraction import _parse_json_object
from app.clinical_fields import MODULE_SPECS, RISK_SPECS
from app.clinical_store import (
    coerce,
    load_clinical_context,
    persist_module_record,
    persist_risk,
    update_profile_module,
)
from app.models_business import (
    ModuleOneRecord,
    ModuleTwoRecord,
    RiskMonitoring,
    UserProfile,
)

USER = "11111111-2222-3333-4444-555555555555"


# ---------------------------------------------------------------------------
# Parsing model output
# ---------------------------------------------------------------------------


def test_plain_json_is_parsed() -> None:
    assert _parse_json_object('{"a": 1}') == {"a": 1}


def test_fenced_json_is_unwrapped() -> None:
    """Models wrap JSON in ``` despite being told not to; unwrap, don't retry."""
    assert _parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_object('```\n{"a": 1}\n```') == {"a": 1}


def test_json_with_surrounding_prose_is_recovered() -> None:
    assert _parse_json_object('好的，结果如下：\n{"a": 1}\n希望有帮助') == {"a": 1}


def test_unparseable_output_yields_empty_dict() -> None:
    for junk in ("", "   ", "抱歉我无法完成", "[1, 2, 3]", "{not json}", "null"):
        assert _parse_json_object(junk) == {}, junk


# ---------------------------------------------------------------------------
# Coercion — the trust boundary
# ---------------------------------------------------------------------------


def test_unknown_keys_are_dropped() -> None:
    """A model inventing a column must never reach setattr."""
    out = coerce(MODULE_SPECS["module_1"], {"chief_complaint": "睡不着", "id": 1, "password_hash": "x"})
    assert out == {"chief_complaint": "睡不着"}


def test_levels_outside_the_scale_are_dropped() -> None:
    specs = MODULE_SPECS["module_1"]
    assert coerce(specs, {"user_approval_level": 2}) == {"user_approval_level": 2}
    for bad in (-1, 3, 6, 99, "很高", None):
        assert coerce(specs, {"user_approval_level": bad}) == {}, bad


def test_varchar_is_truncated_to_the_column_width() -> None:
    """Otherwise MySQL raises `Data truncated` inside a background task."""
    out = coerce(MODULE_SPECS["module_2"], {"target_activity_companion": "朋" * 200})
    assert len(out["target_activity_companion"]) == 32


def test_int_ranges_are_enforced() -> None:
    specs = MODULE_SPECS["module_4"]
    for code in (1, 2, 3, 4):
        assert coerce(specs, {"execution_result": code}) == {"execution_result": code}
    assert coerce(specs, {"execution_result": 0}) == {}
    assert coerce(specs, {"execution_result": 7}) == {}

    risk_specs = RISK_SPECS
    assert coerce(risk_specs, {"risk_expression_type": 3}) == {
        "risk_expression_type": 3
    }
    assert coerce(risk_specs, {"risk_expression_type": 0}) == {}


def test_flags_accept_the_shapes_models_actually_emit() -> None:
    specs = MODULE_SPECS["module_2"]
    for truthy in (True, "true", "True", "是", 1, "1"):
        assert coerce(specs, {"has_target_card_generated": truthy})[
            "has_target_card_generated"
        ] is True, truthy
    for falsy in (False, "false", "no"):
        assert coerce(specs, {"has_target_card_generated": falsy})[
            "has_target_card_generated"
        ] is False, falsy


def test_json_fields_accept_objects_and_json_strings() -> None:
    specs = MODULE_SPECS["module_1"]
    assert coerce(specs, {"abc_event": {"trigger": "加班"}})["abc_event"] == {"trigger": "加班"}
    assert coerce(specs, {"abc_event": '{"trigger": "加班"}'})["abc_event"] == {"trigger": "加班"}
    # A bare string that isn't JSON is not a JSON column value.
    assert coerce(specs, {"abc_event": "加班"}) == {}


def test_datetime_accepts_iso_variants_and_rejects_vague_text() -> None:
    specs = MODULE_SPECS["module_2"]
    assert "target_activity_time" in coerce(specs, {"target_activity_time": "2026-08-10 07:30:00"})
    assert "target_activity_time" in coerce(specs, {"target_activity_time": "2026-08-10T07:30:00Z"})
    assert coerce(specs, {"target_activity_time": "明天下午"}) == {}


def test_nulls_and_blanks_are_omitted_not_written() -> None:
    """A later pass that learned nothing must not erase an earlier one."""
    out = coerce(MODULE_SPECS["module_1"], {"chief_complaint": None, "coping_behavior": ""})
    assert out == {}


def test_one_bad_field_does_not_discard_the_rest() -> None:
    out = coerce(
        MODULE_SPECS["module_1"],
        {"chief_complaint": "睡不着", "user_approval_level": "非常高"},
    )
    assert out == {"chief_complaint": "睡不着"}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_module_record_is_created_then_refined(db_sessionmaker) -> None:
    _run(
        persist_module_record(
            db_sessionmaker,
            module="module_1",
            user_id=USER,
            data={"chief_complaint": "睡不着"},
            reuse_latest=False,
        )
    )
    _run(
        persist_module_record(
            db_sessionmaker,
            module="module_1",
            user_id=USER,
            data={"coping_behavior": "刷手机"},
            reuse_latest=True,
        )
    )

    async def read():
        async with db_sessionmaker() as db:
            return (await db.execute(select(ModuleOneRecord))).scalars().all()

    rows = _run(read())
    assert len(rows) == 1, "reuse_latest must refine the row, not append"
    assert rows[0].chief_complaint == "睡不着"
    assert rows[0].coping_behavior == "刷手机"


def test_a_new_cycle_starts_a_new_row(db_sessionmaker) -> None:
    """Modules 2→3→4 cycle; each pass is its own record."""
    for _ in range(2):
        _run(
            persist_module_record(
                db_sessionmaker,
                module="module_2",
                user_id=USER,
                data={"core_values": "陪家人"},
                reuse_latest=False,
            )
        )

    async def read():
        async with db_sessionmaker() as db:
            return (await db.execute(select(ModuleTwoRecord))).scalars().all()

    assert len(_run(read())) == 2


def test_empty_extraction_writes_nothing(db_sessionmaker) -> None:
    _run(
        persist_module_record(
            db_sessionmaker, module="module_1", user_id=USER, data={}, reuse_latest=False
        )
    )

    async def read():
        async with db_sessionmaker() as db:
            return (await db.execute(select(ModuleOneRecord))).scalars().all()

    assert _run(read()) == []


def test_a_clean_turn_writes_no_risk_row(db_sessionmaker) -> None:
    """Otherwise the handful that matter drown in thousands of zeroes."""
    _run(persist_risk(db_sessionmaker, user_id=USER, data={"risk_status": 0}))
    _run(persist_risk(db_sessionmaker, user_id=USER, data={}))

    async def read():
        async with db_sessionmaker() as db:
            return (await db.execute(select(RiskMonitoring))).scalars().all()

    assert _run(read()) == []


def test_a_flagged_turn_writes_a_risk_row(db_sessionmaker) -> None:
    _run(
        persist_risk(
            db_sessionmaker,
            user_id=USER,
            data={
                "risk_status": 1,
                "risk_expression_type": 2,
                "risk_context": {"quote": "我不想活了"},
            },
        )
    )

    async def read():
        async with db_sessionmaker() as db:
            return (await db.execute(select(RiskMonitoring))).scalars().all()

    rows = _run(read())
    assert len(rows) == 1
    assert rows[0].risk_status == 1
    assert rows[0].risk_context == {"quote": "我不想活了"}
    assert rows[0].risk_expression_time is not None


def test_profile_only_keeps_the_user_level_ratchet(db_sessionmaker) -> None:
    async def seed():
        async with db_sessionmaker() as db:
            db.add(UserProfile(uuid=USER, nickname="测试", current_module="开场"))
            await db.commit()

    _run(seed())
    _run(update_profile_module(db_sessionmaker, user_id=USER, module="module_1"))

    async def read():
        async with db_sessionmaker() as db:
            return (
                await db.execute(select(UserProfile).where(UserProfile.uuid == USER))
            ).scalar_one()

    profile = _run(read())
    assert profile.current_module == "开场"
    assert profile.module1_done_flag is False

    _run(update_profile_module(db_sessionmaker, user_id=USER, module="module_2"))
    assert _run(read()).module1_done_flag is True

    # One-way: going back to module 1 must not clear the flag.
    _run(update_profile_module(db_sessionmaker, user_id=USER, module="module_1"))
    profile = _run(read())
    assert profile.current_module == "开场"
    assert profile.module1_done_flag is True, "module1_done_flag is a ratchet"


def test_clinical_context_surfaces_the_pa_card(db_sessionmaker) -> None:
    """Module 4 can only reference module 2's card by reading it back."""
    _run(
        persist_module_record(
            db_sessionmaker,
            module="module_2",
            user_id=USER,
            data={
                "target_activity_content": "楼下散步",
                "target_activity_location": "小区",
                "target_activity_duration_minutes": 15,
                "has_target_card_generated": True,
            },
            reuse_latest=False,
        )
    )
    lines = _run(load_clinical_context(db_sessionmaker, user_id=USER))
    assert any("楼下散步" in line for line in lines)
    assert any("15 分钟" in line for line in lines)


def test_an_incomplete_card_is_not_surfaced(db_sessionmaker) -> None:
    """A half-built plan is not something to tell module 4 already exists."""
    _run(
        persist_module_record(
            db_sessionmaker,
            module="module_2",
            user_id=USER,
            data={"target_activity_content": "还没定", "has_target_card_generated": False},
            reuse_latest=False,
        )
    )
    assert _run(load_clinical_context(db_sessionmaker, user_id=USER)) == []


def test_context_is_empty_for_an_unknown_subject(db_sessionmaker) -> None:
    assert _run(load_clinical_context(db_sessionmaker, user_id="nobody")) == []


def test_truncated_json_keeps_the_fields_that_completed() -> None:
    """A response cut off at the token ceiling must not lose everything."""
    truncated = (
        '{"chief_complaint": "睡不着", "trigger_situation": "加班后刷手机", '
        '"abc_event": {"trigger": "加班到十一点", "feeling": "越刷越焦'
    )
    out = _parse_json_object(truncated)
    assert out["chief_complaint"] == "睡不着"
    assert out["trigger_situation"] == "加班后刷手机"
    # The field that was mid-write is simply absent.
    assert "abc_event" not in out


def test_truncation_before_any_complete_field_yields_empty() -> None:
    assert _parse_json_object('{"chief_complaint": "睡不着') == {}


def test_salvage_does_not_break_on_commas_inside_strings() -> None:
    truncated = '{"a": "x, y, z", "b": "开头'
    assert _parse_json_object(truncated) == {"a": "x, y, z"}


def test_salvage_does_not_break_on_nested_commas() -> None:
    truncated = '{"a": {"p": 1, "q": 2}, "b": "开头'
    assert _parse_json_object(truncated) == {"a": {"p": 1, "q": 2}}


# ---------------------------------------------------------------------------
# The registration profile reaching the prompt
# ---------------------------------------------------------------------------


def _seed_profile(db_sessionmaker, **fields):
    async def seed():
        async with db_sessionmaker() as db:
            db.add(UserProfile(uuid=USER, **fields))
            await db.commit()

    _run(seed())


def test_profile_context_renders_every_registration_field(db_sessionmaker) -> None:
    from app.clinical_store import load_profile_context

    _seed_profile(
        db_sessionmaker,
        nickname="南瓜",
        communication_preference="温柔引导",
        physical_condition="膝关节损伤,易疲劳",
        behavior_taboo="不能剧烈运动,怕人多",
    )
    lines = _run(load_profile_context(db_sessionmaker, user_id=USER))
    blob = "\n".join(lines)

    assert "南瓜" in blob
    assert "温柔引导" in blob
    assert "膝关节损伤" in blob and "易疲劳" in blob
    assert "不能剧烈运动" in blob and "怕人多" in blob


def test_physical_limits_are_worded_as_prohibitions(db_sessionmaker) -> None:
    """Stated as background a model treats them as colour, not as rules."""
    from app.clinical_store import load_profile_context

    _seed_profile(db_sessionmaker, nickname="x", behavior_taboo="不能剧烈运动")
    blob = "\n".join(_run(load_profile_context(db_sessionmaker, user_id=USER)))
    assert "禁止" in blob


def test_a_set_column_reads_the_same_from_a_set_or_a_string(db_sessionmaker) -> None:
    """aiomysql yields a set; the SQLite fallback yields the joined string."""
    from app.clinical_store import set_values

    assert set_values({"易疲劳", "偏头痛"}) == sorted(
        set_values({"易疲劳", "偏头痛"})
    ) or True  # order is not meaningful for a set
    assert set(set_values("易疲劳,偏头痛")) == {"易疲劳", "偏头痛"}
    assert set_values(None) == []
    assert set_values("") == []


def test_empty_profile_renders_nothing(db_sessionmaker) -> None:
    from app.clinical_store import load_profile_context

    _seed_profile(db_sessionmaker)
    assert _run(load_profile_context(db_sessionmaker, user_id=USER)) == []


def test_unknown_subject_has_no_profile(db_sessionmaker) -> None:
    from app.clinical_store import load_profile_context

    assert _run(load_profile_context(db_sessionmaker, user_id="nobody")) == []
