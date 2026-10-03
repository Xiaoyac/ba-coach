"""Real M2 tool/DB regressions for a zero-rated plan with no obstacles."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.database_v2_schema import metadata as schema
from app.dialogue_confirmation import render_confirmation_summary, summary_present
from app.goal_card_workspace import _plan_fields
from app.plan_contract import (NO_COPING_NEEDED, missing_plan_fields,
    no_coping_required, normalize_coping_evidence)
from app.v2_workflow import runtime_for
from test_goal_card_interaction import (enable_form, invoke, message, open_primary,
    persist_display, review, submit_form, tools)  # noqa: F401
from test_goal_overview import goal_api  # noqa: F401


def na_item(message_id=42, quote="基本没有什么阻力"):
    return {"status": "not_applicable", "source_message_id": message_id, "source_quote": quote}


def ready_plan():
    return {"activity_content": "打羽毛球", "schedule_text": "明天晚上",
        "duration_minutes": 120, "difficulty_rating": 0,
        "difficulty_evidence": {"rating": {"value": 0, "message_id": 42, "quote": "难度0分"}},
        "potential_barriers": ["基本没有什么阻力"], "barrier_coping_plan": [na_item()]}


def test_source_bound_no_obstacle_shape_preserves_zero_and_renders_no_invented_coping():
    plan = ready_plan()
    sources = [SimpleNamespace(id=42, role="user", content="难度0分，基本没有什么阻力")]
    plan = normalize_coping_evidence(plan, sources)
    assert no_coping_required(plan)
    assert missing_plan_fields(plan) == []
    assert "plan" not in plan["barrier_coping_plan"][0]
    rendered = render_confirmation_summary("module_2", plan)
    assert "0/10" in rendered and NO_COPING_NEEDED in rendered
    assert summary_present("module_2", rendered, plan)
    assert not summary_present("module_2", rendered.replace(NO_COPING_NEEDED, "尚未说明"), plan)
    assert _plan_fields(plan)["barrier_coping_plan"] == NO_COPING_NEEDED


@pytest.mark.parametrize("case", ["missing", "assistant", "different_quote", "different_barrier", "mixed", "invented_plan"])
def test_no_obstacle_assessment_cannot_bypass_source_and_shape_checks(case):
    plan = ready_plan()
    sources = [SimpleNamespace(id=42, role="user", content="基本没有什么阻力")]
    if case == "missing":
        plan["barrier_coping_plan"][0]["source_message_id"] = 999
    elif case == "assistant":
        sources[0].role = "assistant"
    elif case == "different_quote":
        sources[0].content = "下雨的话就不去了"
    elif case == "different_barrier":
        plan["potential_barriers"] = ["下雨"]
    elif case == "mixed":
        plan["barrier_coping_plan"].append({"barrier": "下雨", "plan": "改在室内"})
    else:
        plan["barrier_coping_plan"][0]["plan"] = "程序编出的应对"
    with pytest.raises(ValueError):
        normalize_coping_evidence(plan, sources)


@pytest.mark.parametrize("barriers", [[], ["下雨"], ["用户明确表示基本没有什么阻力"]])
def test_empty_coping_remains_unknown_without_explicit_sourced_not_applicable(barriers):
    plan = {**ready_plan(), "potential_barriers": barriers, "barrier_coping_plan": []}
    assert "barrier_coping_plan" in missing_plan_fields(plan)
    assert not no_coping_required(plan)


async def zero_rated_form(client, db):
    _, _, card = await open_primary(db, "我愿意把打羽毛球作为核心目标。")
    user, executor, card = await submit_form(client, db, card, {
        "activity_content": "打羽毛球", "schedule_text": "明天晚上", "location": "羽毛球馆",
        "duration_minutes": 120, "frequency_text": "暂定只有这一场了", "difficulty_rating": 0,
        "potential_barriers": "基本没有什么阻力", "barrier_coping_plan": ""})
    payload = {"target_activity_content": "打羽毛球", "schedule_text": "明天晚上",
        "target_activity_location": "羽毛球馆", "target_activity_duration_minutes": 120,
        "frequency_rule": {"schema_version": 1, "text": "暂定只有这一场了"}, "difficulty_rating": 0,
        "difficulty_evidence": {"rating": {"message_id": user.id,
            "quote": "我评估的执行难度（0–10）：0", "score_text": "0"}},
        "potential_barriers": ["基本没有什么阻力"], "barrier_coping_plan": [],
        "goal_proposal": {"selection_status": "selected", "selection_role": "core", "goal_kind": "primary",
            "selection_message_id": user.id, "selection_quote": user.content, "activity_quote": "打羽毛球"}}
    saved = await invoke(executor, "save_pa_card", data=payload)
    assert saved["status"] == "draft_saved", saved
    return user, executor, saved["goal_card"]


async def test_review_query_and_present_report_same_real_missing_item(goal_api):
    client, db, _ = goal_api
    _, executor, card = await zero_rated_form(client, db)
    checked = await review(executor, card)
    assert checked["status"] == "goal_card_reviewed", checked
    assert not checked["ready"]
    assert checked["readiness"]["missing_fields"] == ["barrier_coping_plan"]
    assert checked["readiness"]["blockers"] == []
    snapshot = await invoke(executor, "get_pa_card")
    assert snapshot["goal_card_readiness"] == checked["readiness"]
    presented = await invoke(executor, "present_pa_card")
    assert presented["status"] == "blocked"
    assert presented["readiness"] == checked["readiness"]
    assert snapshot["draft"]["difficulty_rating"] == 0


async def test_sourced_no_obstacle_repair_reviews_and_displays_in_one_call_then_requires_consent(goal_api):
    client, db, _ = goal_api
    user, executor, card = await zero_rated_form(client, db)
    saved = await invoke(executor, "save_pa_card", data={"target_activity_content": "打羽毛球",
        "goal_proposal": None, "potential_barriers": ["基本没有什么阻力"],
        "barrier_coping_plan": [na_item(user.id)]})
    assert saved["status"] == "draft_saved", saved
    checked = await review(executor, saved["goal_card"])
    assert checked["status"] == "ready_to_display", checked
    assert checked["goal_card"]["phase"] == "ready"
    assert checked["ready"] and NO_COPING_NEEDED in checked["display_text"]
    await db.rollback()
    plan = (await db.execute(select(schema.tables["module_two_record"]))).mappings().one()
    assert plan["record_status"] == "draft" and plan["confirmation_message_id"] is None
    assert plan["difficulty_rating"] == 0 and no_coping_required(plan)
    _, state = await runtime_for(db, "chat-a")
    assert state["current_module"] == "module_2"
    # An identical review/presentation within the turn must not advance the
    # UI version again, nor take another provider request to obtain a card.
    again = await review(executor, checked["goal_card"])
    assert again["status"] == "ready_to_display", again
    assert again["goal_card"]["revision"] == checked["goal_card"]["revision"]
    await persist_display(db, checked["display_text"])
    consent = await message(db, "确认，就按这个计划试试。")
    confirmed = await invoke(tools(db, consent.id), "confirm_pa_card")
    assert confirmed["status"] == "confirmed", confirmed
    assert confirmed["goal_card"]["phase"] == "confirmed"
    await db.rollback()
    _, state = await runtime_for(db, "chat-a")
    assert state["current_module"] == "module_3"


async def test_forged_not_applicable_tool_source_cannot_change_saved_draft(goal_api):
    client, db, _ = goal_api
    _, executor, _ = await zero_rated_form(client, db)
    rejected = await invoke(executor, "save_pa_card", data={"target_activity_content": "打羽毛球",
        "goal_proposal": None, "potential_barriers": ["基本没有什么阻力"],
        "barrier_coping_plan": [na_item(999999)]})
    assert rejected["status"] == "blocked" and "invalid_no_barrier_source" in rejected["reason"]
    snapshot = await invoke(executor, "get_pa_card")
    assert snapshot["draft"]["barrier_coping_plan"] == []


async def test_last_allowed_review_call_emits_actual_card_without_another_model_call(goal_api):
    from app.pa_tool_loop import PAToolReply
    from app.providers.base import Message, StreamDelta
    client, db, _ = goal_api
    user, executor, _ = await zero_rated_form(client, db)

    class Provider:
        calls = 0

        async def stream_tools(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                name, args = "get_pa_card", {}
            elif self.calls == 2:
                prior = json.loads(kwargs["messages"][-1]["content"])
                name, args = "save_pa_card", {"state_version": prior["state_version"], "data": {
                    "target_activity_content": "打羽毛球", "goal_proposal": None,
                    "potential_barriers": ["基本没有什么阻力"], "barrier_coping_plan": [na_item(user.id)]}}
            elif self.calls == 3:
                prior = json.loads(kwargs["messages"][-1]["content"])
                card = prior["goal_card"]
                name, args = "review_goal_card", {"state_version": prior["state_version"],
                    "card_id": card["id"], "card_revision": card["revision"], "is_pa": True,
                    "near_term": True, "manageable": True, "concerns": []}
            else:
                pytest.fail("A complete reviewed card must not require another provider request")
            yield StreamDelta(kind="tool_calls", tool_calls=[{"id": f"step-{self.calls}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}])

    provider = Provider()
    loop = PAToolReply(provider, executor, max_rounds=3, telemetry={})
    emitted = [delta async for delta in loop.stream(system="policy", messages=[Message(role="user", content=user.content)])]
    visible = "".join(delta.text for delta in emitted if delta.kind == "content")
    assert provider.calls == 3 and NO_COPING_NEEDED in visible and "这份安排可以吗" in visible
    assert executor.trace[-1]["name"] == "review_goal_card"
    assert executor.trace[-1]["result"]["status"] == "ready_to_display"
    await db.rollback()
    _, state = await runtime_for(db, "chat-a")
    assert state["current_module"] == "module_2", "Rendering a card is not consent"
