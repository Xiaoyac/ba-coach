"""Main reply gets source-labelled business facts, not another coaching script."""
import json

import pytest
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.m1_contract import VERSION as M1_VERSION
from app.m4_contract import VERSION as M4_VERSION
from app.prompts import build_system_segments
from app.v2_workflow import clinical_context
from test_goal_overview import goal_api


async def prompt_for(db, module):
    context = await clinical_context(async_sessionmaker(db.bind, expire_on_commit=False), "a", "chat-a")
    segments = build_system_segments(module, global_prompt="全局策略", module_prompt="模块策略",
                                     clinical_context=context)
    return context, "\n".join(segment.text for segment in segments)


async def test_m1_prompt_keeps_expressions_and_sources_without_missing_field_script(goal_api):
    _, db, _ = goal_api
    contract = {
        "version": M1_VERSION, "session_id": "chat-a", "path": "low_disclosure",
        "missing_fields": ["PRIVATE_MISSING_FIELD"], "completed_steps": ["PRIVATE_STEP"],
        "education_missing_topics": ["PRIVATE_EDUCATION_GAP"],
        "validation_issues": [{"reason": "PRIVATE_DIAGNOSTIC"}],
        "milestones": {"PRIVATE_MILESTONE": False}, "next_action": "PRIVATE_TASK",
        "understanding_verified": False, "core_questions_resolved": False,
        "goal_consent_expressed": True, "disclosure_declined": True,
        "limitation_explained": True, "limitation_acknowledged": True,
        "evidence": {
            "education_0": {"role": "assistant", "turn": 4, "quote": "情绪和行动会相互影响。"},
            "consent": {"role": "user", "turn": 5, "quote": "我愿意一起讨论目标。"},
            "refusal": {"role": "user", "turn": 2, "quote": "暂时不想讲这件事。"},
        },
    }
    rt = schema.tables["conversation_runtime_states"]
    await db.execute(update(rt).where(rt.c.conversation_id == 1).values(current_module="module_1"))
    await db.execute(insert(schema.tables["module_one_record"]), {
        "id": "draft", "user_id": "a", "version_no": 7, "record_status": "draft",
        "chief_complaint": "用户原有困扰", "event_experience": {"_m1_contract": contract},
    })
    await db.commit()
    lines, prompt = await prompt_for(db, "module_1")
    status = json.loads(next(line for line in lines if line.startswith("M1 已记录")).split("：", 1)[1])
    assert status["record_id"] == "draft" and status["version_no"] == 7
    assert status["record_status"] == "draft" and status["evidence_version"] == M1_VERSION
    assert status["explained_contents"][0]["source"] == contract["evidence"]["education_0"]
    assert status["user_expressions"]["consent"] == contract["evidence"]["consent"]
    assert status["disclosure_declined"] is True and status["goal_consent_expressed"] is True
    assert "用户原有困扰" in prompt and "暂时不想讲这件事" in prompt
    for forbidden in ("PRIVATE_", "M1对话节奏", "才补问", "如果缺的是教育内容",
                      "missing_fields", "education_missing_topics", "completed_steps", "next_action"):
        assert forbidden not in prompt


@pytest.mark.parametrize("module", ["module_2", "module_3"])
async def test_unbound_and_legacy_states_are_factual_without_coaching_instructions(goal_api, module):
    _, db, _ = goal_api
    rt = schema.tables["conversation_runtime_states"]
    await db.execute(update(rt).where(rt.c.conversation_id == 1).values(current_module=module))
    await db.commit()
    lines, prompt = await prompt_for(db, module)
    legacy = json.loads(next(line for line in lines if line.startswith("M1 历史状态")).split("：", 1)[1])
    assert legacy["completion_source"] == "legacy_imported" and legacy["evidence_status"] == "missing"
    assert legacy["confirmed_formulation_id"] is None
    assert '"active_goal_id": null' in prompt
    for forbidden in ("继续目标设定", "不要求重做", "可以与用户讨论", "才由 Agent 流程创建"):
        assert forbidden not in prompt


async def test_m4_prompt_retains_bound_versions_and_confirmation_without_gap_tasks(goal_api):
    _, db, _ = goal_api
    await db.execute(insert(schema.tables["module_two_record"]), {
        "id": "p1", "goal_id": "g1", "version_no": 4, "timezone": "Asia/Shanghai",
        "record_status": "confirmed", "confirmation_status": "confirmed", "confirmation_message_id": 21,
        "activity_content": "楼下散步", "schedule_text": "晚饭后", "duration_minutes": 10,
    })
    await db.execute(insert(schema.tables["pa_cycles"]), {
        "id": "cycle", "goal_id": "g1", "ordinal": 2, "module_two_record_id": "p1",
    })
    await db.execute(insert(schema.tables["module_four_record"]), {
        "id": "review", "cycle_id": "cycle", "record_status": "draft", "scenario_type": "A",
        "execution_result": 1, "chain_confirmation_status": "confirmed", "confirmation_message_id": 25,
        "phase_c": {"_m4_contract": {"version": M4_VERSION, "cycle_id": "cycle",
            "missing_fields": ["PRIVATE_M4_GAP"], "completed_steps": ["PRIVATE_M4_STEP"],
            "next_action": "PRIVATE_M4_TASK"}},
    })
    rt = schema.tables["conversation_runtime_states"]
    await db.execute(update(rt).where(rt.c.conversation_id == 1).values(
        current_module="module_4", active_goal_id="g1", active_cycle_id="cycle"))
    await db.commit()
    lines, prompt = await prompt_for(db, "module_4")
    plan = json.loads(next(line for line in lines if line.startswith("本周期绑定计划")).split("：", 1)[1])
    assert plan["cycle_id"] == "cycle" and plan["version_no"] == 4
    assert plan["confirmation_status"] == "confirmed" and plan["confirmation_message_id"] == 21
    review = json.loads(next(line for line in lines if line.startswith("当前周期 M4")).split("：", 1)[1])
    assert review["record_status"] == "draft" and review["chain_confirmation_status"] == "confirmed"
    assert review["confirmation_message_id"] == 25 and review["evidence_version"] == M4_VERSION
    assert "楼下散步" in prompt and "PRIVATE_" not in prompt
    assert "missing_fields" not in prompt and "completed_steps" not in prompt
