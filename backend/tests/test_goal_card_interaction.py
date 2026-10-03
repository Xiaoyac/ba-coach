"""User-visible M2 card lifecycle through real APIs and native tools.

Isolated SQLite and deterministic user messages exercise persistence and
confirmation without asking a live model to declare its own tests successful.
"""
import json

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database_v2_schema import metadata as schema
from app.goal_card_interaction import finalize_ui_display, validate_card_action
from app.goal_card_workspace import bind_submission, read_card_row
from app.models import ConversationMessage
from app.pa_card_tools import PACardTools, finalize_tool_display
from app.v2_workflow import runtime_for
from test_goal_overview import goal_api  # noqa: F401
from test_turn_confirmation_0924 import setup_turn


@pytest.fixture(autouse=True)
def enable_form(monkeypatch):
    from app.config import get_settings
    settings = get_settings().model_copy(update={"goal_card_ui_enabled": True,
        "pa_card_tools_enabled": True, "database_schema_version": "v2"})
    monkeypatch.setattr("app.config.get_settings", lambda: settings)


async def message(db, text, role="user", conversation_id=1):
    position = await db.scalar(select(func.max(ConversationMessage.position)).where(
        ConversationMessage.conversation_id == conversation_id))
    row = ConversationMessage(conversation_id=conversation_id,
        position=(position + 1 if position is not None else 0), role=role, content=text)
    db.add(row)
    await db.commit()
    return row


def tools(db, boundary):
    return PACardTools(maker=async_sessionmaker(db.bind, expire_on_commit=False),
        session_id="chat-a", user_id="a", user_message_id=boundary,
        module="module_2", ui_enabled=True)


async def invoke(executor, name, **args):
    if name != "get_pa_card" and "state_version" not in args:
        snapshot = await invoke(executor, "get_pa_card")
        assert snapshot["status"] == "ok", snapshot
        args["state_version"] = snapshot["state_version"]
    return await executor.execute({"id": f"call-{len(executor.trace)}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}})


async def open_primary(db, text="我愿意试试散步，先一起定个目标。", kind="primary"):
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(current_module="module_2"))
    user = await message(db, text)
    executor = tools(db, user.id)
    result = await invoke(executor, "open_goal_card", kind=kind,
        source_message_id=user.id, source_quote=text)
    assert result["status"] == "goal_card_opened", result
    return user, executor, result["goal_card"]


async def submit_form(client, db, card, fields):
    response = await client.put("/api/program/chat-a/goal-card", json={
        "card_id": card["id"], "revision": card["revision"], "fields": fields})
    assert response.status_code == 200, response.text
    payload = response.json()
    user = await message(db, payload["submission_text"])
    conversation, _ = await runtime_for(db, "chat-a")
    assert await bind_submission(db, conversation, user.id)
    await db.commit()
    return user, tools(db, user.id), payload["card"]


async def review(executor, card, *, is_pa=True, near_term=True, manageable=True, concerns=None):
    return await invoke(executor, "review_goal_card", card_id=card["id"],
        card_revision=card["revision"], is_pa=is_pa, near_term=near_term,
        manageable=manageable, concerns=concerns or [])


async def persist_display(db, text):
    assistant = await message(db, text, "assistant")
    await finalize_tool_display(db, session_id="chat-a", user_id="a", assistant_message_id=assistant.id)
    await finalize_ui_display(db, session_id="chat-a", user_id="a", assistant_message_id=assistant.id)
    await db.commit()
    return assistant


async def business_snapshot(db):
    return {name: [dict(row) for row in (await db.execute(select(schema.tables[name]))).mappings()]
        for name in ("pa_goals", "pa_cycles", "pa_cycle_progress", "module_two_record", "conversation_runtime_states")}


async def test_module_entry_and_unwilling_conversation_do_not_open_form(goal_api):
    client, db, _ = goal_api
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(current_module="module_2"))
    user = await message(db, "我暂时不想制定活动目标，想先聊聊为什么要做。")
    executor = tools(db, user.id)
    snapshot = await invoke(executor, "get_pa_card")
    assert snapshot["goal_card"] is None
    result = await executor.execute({"id": "continue", "type": "function", "function": {
        "name": "continue_pa_conversation", "arguments": "{}"}})
    assert result["status"] == "continue_conversation"
    assert (await client.get("/api/program/chat-a/goal-card")).json()["card"] is None
    assert await db.scalar(select(func.count()).select_from(schema.tables["pa_cycles"])) == 0


@pytest.mark.parametrize("source_kind", ["assistant", "foreign", "missing", "fabricated_quote"])
async def test_form_stage_tool_rejects_non_user_or_invented_source(goal_api, source_kind):
    _, db, _ = goal_api
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(current_module="module_2"))
    candidate = await message(db, "我愿意制定散步目标。",
        role="assistant" if source_kind == "assistant" else "user",
        conversation_id=3 if source_kind == "foreign" else 1)
    boundary = await message(db, "先说说看。")
    result = await invoke(tools(db, boundary.id), "open_goal_card", kind="primary",
        source_message_id=99999 if source_kind == "missing" else candidate.id,
        source_quote="不存在的原话" if source_kind == "fabricated_quote" else candidate.content)
    assert result["status"] == "blocked", result
    await db.rollback()
    assert await db.scalar(select(func.count()).select_from(schema.tables["goal_card_workspaces"])) == 0


async def test_partial_form_discussion_review_display_and_real_confirmation(goal_api):
    client, db, _ = goal_api
    _, _, card = await open_primary(db)
    form_user, _, card = await submit_form(client, db, card, {"activity_content": "散步"})
    assert card["fields"]["difficulty_rating"] is None
    assert await db.scalar(select(func.count()).select_from(schema.tables["module_two_record"])) == 0
    await message(db, "散步可以。你已经想到了活动，接下来按你的安排一起细化。", "assistant")
    text = "我选散步作为核心目标，明天晚饭后在小区走十分钟，先做一次，难度4分，下雨就在室内走。"
    user = await message(db, text)
    executor = tools(db, user.id)
    result = await invoke(executor, "save_pa_card", data={
        "target_activity_content": "散步", "schedule_text": "明天晚饭后", "target_activity_location": "小区",
        "target_activity_duration_minutes": 10, "difficulty_rating": 4,
        "difficulty_evidence": {"rating": {"message_id": user.id, "quote": "难度4分", "score_text": "4"}},
        "potential_barriers": ["下雨"], "barrier_coping_plan": [{"barrier": "下雨", "plan": "室内走"}],
        "goal_proposal": {"selection_status": "selected", "selection_role": "core", "goal_kind": "primary",
            "selection_message_id": user.id, "selection_quote": text, "activity_quote": "散步"}})
    assert result["status"] == "draft_saved", result
    card = result["goal_card"]
    assert card["phase"] == "discussing" and card["fields"]["difficulty_rating"] == 4
    blocked = await invoke(executor, "present_pa_card")
    assert blocked["status"] == "blocked", blocked
    checked = await review(executor, card)
    assert checked["ready"], checked
    presented = await invoke(executor, "present_pa_card")
    assert presented["status"] == "ready_to_display", presented
    assert presented["goal_card"]["phase"] == "ready"
    await db.rollback()
    row = await read_card_row(db, 1, "a")
    assert row["submission_message_id"] == form_user.id
    assert row["display_assistant_message_id"] is None
    assert (await client.get("/api/program/goals/overview")).json()["goals"][-1].get("plan") is None
    assistant = await persist_display(db, presented["display_text"])
    row = await read_card_row(db, 1, "a")
    assert row["display_assistant_message_id"] == assistant.id
    user = await message(db, "确认，就按这个计划试试。")
    result = await invoke(tools(db, user.id), "confirm_pa_card")
    assert result["status"] == "confirmed", result
    assert result["goal_card"]["phase"] == "confirmed"
    await db.rollback()
    _, runtime = await runtime_for(db, "chat-a")
    assert runtime["current_module"] == "module_3"
    plan = (await db.execute(select(schema.tables["module_two_record"]))).mappings().one()
    assert plan["confirmation_message_id"] == user.id and plan["record_status"] == "confirmed"
    overview = (await client.get("/api/program/goals/overview")).json()
    saved = next(g for g in overview["goals"] if g["id"] == runtime["active_goal_id"])
    assert saved["plan"]["activity_content"] == "散步"


@pytest.mark.parametrize("is_pa,near_term,manageable,rating,concerns", [
    (False, True, True, 4, ["活动不是身体活动"]),
    (True, False, True, 4, ["首次活动安排过远"]),
    (True, True, False, 8, ["用户认为难度过大"]),
    (True, True, True, 8, []),
])
async def test_non_pa_far_or_high_difficulty_returns_to_discussion(goal_api, is_pa, near_term, manageable, rating, concerns):
    client, db, _ = goal_api
    _, _, card = await open_primary(db)
    _, executor, card = await submit_form(client, db, card,
        {"activity_content": "散步" if is_pa else "阅读", "schedule_text": "下个月" if not near_term else "明天",
         "difficulty_rating": rating})
    result = await review(executor, card, is_pa=is_pa, near_term=near_term,
        manageable=manageable, concerns=concerns)
    assert result["status"] == "goal_card_reviewed" and result["ready"] is False
    assert result["goal_card"]["phase"] == "discussing"
    assert (await invoke(executor, "present_pa_card"))["status"] == "blocked"
    assert (await invoke(executor, "confirm_pa_card"))["status"] == "blocked"
    assert await db.scalar(select(func.count()).select_from(schema.tables["pa_cycles"])) == 0


async def test_empty_activity_is_not_ready_even_with_low_difficulty_and_time(goal_api):
    client, db, _ = goal_api
    _, _, card = await open_primary(db)
    _, executor, card = await submit_form(client, db, card,
        {"schedule_text": "明天", "difficulty_rating": 0})
    result = await review(executor, card)
    assert result["status"] == "goal_card_reviewed", result
    assert result["ready"] is False, "A review of a blank activity cannot offer a ready PA card"


@pytest.mark.parametrize("field,value", [("near_term", 1), ("manageable", 0), ("is_pa", 1)])
async def test_review_json_scalar_types_cannot_be_coerced_to_success(goal_api, field, value):
    client, db, _ = goal_api
    _, _, card = await open_primary(db)
    _, executor, card = await submit_form(client, db, card,
        {"activity_content": "散步", "schedule_text": "明天", "difficulty_rating": 0})
    args = {"is_pa": True, "near_term": True, "manageable": True, field: value}
    result = await review(executor, card, **args)
    assert result["status"] == "blocked", result
    await db.rollback()
    assert (await read_card_row(db, 1, "a"))["revision"] == card["revision"]


async def test_secondary_minimal_confirmation_preserves_core_and_enters_my_goals(goal_api):
    client, db, _ = goal_api
    await setup_turn(db, "核心计划已经确认。", module="module_3")
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(current_module="module_2"))
    await db.commit()
    before = await business_snapshot(db)
    _, _, card = await open_primary(db, "我想保留原核心目标，额外加一项骑车。", kind="secondary")
    _, executor, card = await submit_form(client, db, card, {"activity_content": "骑车"})
    checked = await review(executor, card, near_term=None, manageable=None)
    assert checked["ready"], checked
    presented = await invoke(executor, "present_secondary_goal_card", card_id=card["id"],
        card_revision=checked["goal_card"]["revision"])
    assert presented["status"] == "ready_to_display", presented
    assert "我的目标" in presented["display_text"]
    await persist_display(db, presented["display_text"])
    user = await message(db, "确认，就按这个计划试试。")
    result = await invoke(tools(db, user.id), "confirm_secondary_goal_card", card_id=card["id"],
        card_revision=presented["goal_card"]["revision"])
    assert result["status"] == "secondary_confirmed", result
    assert result["core_unchanged"] and result["saved_location"] == "我的目标"
    await db.rollback()
    assert await business_snapshot(db) == before
    overview = (await client.get("/api/program/goals/overview")).json()
    assert overview["formulation_cards"][0]["id"] == card["id"]
    assert overview["formulation_cards"][0]["fields"]["activity_content"] == "骑车"


async def test_secondary_source_update_invalidates_review_and_rejects_unspoken_value(goal_api):
    client, db, _ = goal_api
    _, _, card = await open_primary(db, "我想额外加一项骑车，保留原核心目标。", kind="secondary")
    _, executor, card = await submit_form(client, db, card, {"activity_content": "骑车"})
    checked = await review(executor, card, near_term=None, manageable=None)
    user = await message(db, "骑车改成散步，在公园。")
    executor = tools(db, user.id)
    args = {"card_id": card["id"], "card_revision": checked["goal_card"]["revision"],
        "updates": [{"field": "activity_content", "value": "跑步", "source_message_id": user.id, "source_quote": user.content}]}
    assert (await invoke(executor, "update_secondary_goal_card", **args))["status"] == "blocked"
    args["updates"][0]["value"] = "散步"
    updated = await invoke(executor, "update_secondary_goal_card", **args)
    assert updated["status"] == "goal_card_updated", updated
    assert updated["goal_card"]["fields"]["activity_content"] == "散步"
    assert (await invoke(executor, "present_secondary_goal_card", card_id=card["id"],
        card_revision=updated["goal_card"]["revision"]))["status"] == "blocked"


@pytest.mark.parametrize("case", ["unpersisted_display", "different_display", "refusal", "amendment", "newer_message"])
async def test_secondary_confirmation_requires_display_and_actual_unamended_consent(goal_api, case):
    client, db, _ = goal_api
    _, _, card = await open_primary(db, "原来的核心目标不变，我额外想骑车。", kind="secondary")
    _, executor, card = await submit_form(client, db, card, {"activity_content": "骑车"})
    checked = await review(executor, card, near_term=None, manageable=None)
    presented = await invoke(executor, "present_secondary_goal_card", card_id=card["id"],
        card_revision=checked["goal_card"]["revision"])
    assert presented["status"] == "ready_to_display", presented
    if case != "unpersisted_display":
        await persist_display(db, "先聊聊别的。" if case == "different_display" else presented["display_text"])
    if case == "newer_message":
        await message(db, "先等等，我还在想。")
    text = {"refusal": "先不确认", "amendment": "可以，但是改成明天"}.get(case, "确认，就按这个计划试试。")
    user = await message(db, text)
    result = await invoke(tools(db, user.id), "confirm_secondary_goal_card", card_id=card["id"],
        card_revision=presented["goal_card"]["revision"])
    assert result["status"] == "blocked", result
    await db.rollback()
    assert (await read_card_row(db, 1, "a"))["phase"] == "ready"
    assert not (await client.get("/api/program/goals/overview")).json()["formulation_cards"]


async def test_old_display_finalization_cannot_overwrite_edited_form(goal_api):
    client, db, _ = goal_api
    _, _, card = await open_primary(db, "我想保留原核心并额外骑车。", kind="secondary")
    _, executor, card = await submit_form(client, db, card, {"activity_content": "骑车"})
    checked = await review(executor, card, near_term=None, manageable=None)
    presented = await invoke(executor, "present_secondary_goal_card", card_id=card["id"],
        card_revision=checked["goal_card"]["revision"])
    assistant = await message(db, presented["display_text"], "assistant")
    response = await client.put("/api/program/chat-a/goal-card", json={"card_id": card["id"],
        "revision": presented["goal_card"]["revision"], "fields": {"activity_content": "游泳"}})
    assert response.status_code == 200, response.text
    await finalize_ui_display(db, session_id="chat-a", user_id="a", assistant_message_id=assistant.id)
    await db.commit()
    row = await read_card_row(db, 1, "a")
    assert row["phase"] == "discussing" and row["display_assistant_message_id"] is None
    assert row["fields"]["activity_content"] == "游泳"
    user = await message(db, "确认")
    result = await invoke(tools(db, user.id), "confirm_secondary_goal_card", card_id=card["id"],
        card_revision=presented["goal_card"]["revision"])
    assert result["status"] == "blocked", result


@pytest.mark.parametrize("kind", ["primary", "secondary"])
async def test_explicit_confirmation_turn_cannot_re_review_and_invalidate_clicked_version(goal_api, kind):
    from app.goal_card_interaction import bind_card_action
    from app.goal_card_workspace import sync_core_card
    client, db, _ = goal_api
    if kind == "primary":
        await setup_turn(db, "我想继续细化散步目标。")
        executor = tools(db, 21)
        opened = await invoke(executor, "open_goal_card", kind=kind,
            source_message_id=21, source_quote="我想继续细化散步目标。")
        assert opened["status"] == "goal_card_opened", opened
        conversation, state = await runtime_for(db, "chat-a")
        plan = (await db.execute(select(schema.tables["module_two_record"]))).mappings().one()
        card = await sync_core_card(db, conversation, state, "discussing", plan)
        await db.commit()
        checked = await review(executor, card)
        assert checked["ready"], checked
        presented = await invoke(executor, "present_pa_card")
    else:
        _, _, card = await open_primary(db, "我保留原核心目标，额外想骑车。", kind=kind)
        _, executor, card = await submit_form(client, db, card, {"activity_content": "骑车"})
        checked = await review(executor, card, near_term=None, manageable=None)
        presented = await invoke(executor, "present_secondary_goal_card", card_id=card["id"],
            card_revision=checked["goal_card"]["revision"])
    assert presented["status"] == "ready_to_display", presented
    assistant = await persist_display(db, presented["display_text"])
    card = presented["goal_card"]
    clicked = {"goal_card_action": "confirm", "goal_card_id": card["id"],
        "goal_card_revision": str(card["revision"])}
    text = f"确认这张目标卡（第 {card['revision']} 版），就按这个安排。"
    await validate_card_action(db, session_id="chat-a", user_id="a", metadata=clicked, text=text)
    user = await message(db, text)
    conversation, _ = await runtime_for(db, "chat-a")
    await bind_card_action(db, conversation, user.id, clicked)
    await db.commit()
    executor = tools(db, user.id)
    # The version-bearing UI sentence uses the existing semantic consent
    # classifier. Stub just that language interpretation, retaining all real
    # database, source, display, and version checks around it.
    class ConfirmationProvider:
        async def route(self, *, system, user, max_tokens):
            payload = json.loads(user)
            assert payload["user_response"] == text
            assert payload["assistant_proposal"] == presented["display_text"]
            return json.dumps({"intent": "confirm", "source_quote": text,
                "amendment": False, "unresolved": False})
    executor.provider = ConfirmationProvider()
    snapshot = await invoke(executor, "get_pa_card")
    assert snapshot["goal_card_action"] == {"card_id": card["id"], "revision": card["revision"], "action": "confirm"}
    expected_confirm = "confirm_pa_card" if kind == "primary" else "confirm_secondary_goal_card"
    assert {tool["function"]["name"] for tool in executor.available_tools()} == {"get_pa_card", expected_confirm}
    # A model can still propose a redundant review despite a clear button
    # click. It must not destroy the version the user just consented to.
    attempted = await review(executor, card,
        near_term=True if kind == "primary" else None,
        manageable=True if kind == "primary" else None)
    assert attempted["status"] == "blocked", attempted
    await db.rollback()
    durable = await read_card_row(db, 1, "a")
    assert durable["revision"] == card["revision"] and durable["phase"] == "ready"
    assert durable["display_assistant_message_id"] == assistant.id
    args = {} if kind == "primary" else {"card_id": card["id"], "card_revision": card["revision"]}
    confirmed = await invoke(executor, "confirm_pa_card" if kind == "primary" else "confirm_secondary_goal_card", **args)
    assert confirmed["status"] == ("confirmed" if kind == "primary" else "secondary_confirmed"), confirmed


async def test_plain_chat_correction_of_ready_card_keeps_editing_tools_available(goal_api):
    client, db, _ = goal_api
    _, _, card = await open_primary(db, "原核心目标不变，我另外想骑车。", kind="secondary")
    _, executor, card = await submit_form(client, db, card, {"activity_content": "骑车"})
    checked = await review(executor, card, near_term=None, manageable=None)
    presented = await invoke(executor, "present_secondary_goal_card", card_id=card["id"],
        card_revision=checked["goal_card"]["revision"])
    await persist_display(db, presented["display_text"])
    user = await message(db, "骑车改成散步。")
    executor = tools(db, user.id)
    snapshot = await invoke(executor, "get_pa_card")
    assert snapshot["goal_card_action"] is None
    assert "update_secondary_goal_card" in {tool["function"]["name"] for tool in executor.available_tools()}
    changed = await invoke(executor, "update_secondary_goal_card", card_id=card["id"],
        card_revision=presented["goal_card"]["revision"], updates=[{"field": "activity_content", "value": "散步",
            "source_message_id": user.id, "source_quote": user.content}])
    assert changed["status"] == "goal_card_updated", changed
    assert changed["goal_card"]["phase"] == "discussing"
    assert changed["goal_card"]["fields"]["activity_content"] == "散步"


async def test_stale_form_action_and_pause_never_confirm_or_erase_draft(goal_api):
    from fastapi import HTTPException
    client, db, _ = goal_api
    _, _, card = await open_primary(db)
    user, executor, card = await submit_form(client, db, card,
        {"activity_content": "散步", "schedule_text": "明天", "difficulty_rating": 4})
    checked = await review(executor, card)
    response = await client.put("/api/program/chat-a/goal-card", json={"card_id": card["id"],
        "revision": checked["goal_card"]["revision"], "fields": {"activity_content": "骑车"}})
    assert response.status_code == 200, response.text
    with pytest.raises(HTTPException) as failure:
        await validate_card_action(db, session_id="chat-a", user_id="a", text="确认",
            metadata={"goal_card_action": "confirm", "goal_card_id": card["id"],
                      "goal_card_revision": str(checked["goal_card"]["revision"])})
    assert failure.value.status_code == 409
    stop = await message(db, "不用继续细化了，先放在这里吧。")
    executor = tools(db, stop.id)
    bad = await invoke(executor, "pause_goal_card", source_message_id=user.id, source_quote=user.content)
    assert bad["status"] == "blocked"
    paused = await invoke(executor, "pause_goal_card", source_message_id=stop.id, source_quote=stop.content)
    assert paused["status"] == "goal_card_paused", paused
    assert paused["goal_card"]["phase"] == "paused" and paused["goal_card"]["fields"]["activity_content"] == "骑车"
    assert (await invoke(executor, "save_pa_card", data={"target_activity_content": "骑车", "goal_proposal": None}))["status"] == "blocked"
    assert (await invoke(executor, "confirm_pa_card"))["status"] == "blocked"
    assert (await client.get("/api/program/chat-a/goal-card")).json()["card"]["phase"] == "paused"
    assert await db.scalar(select(func.count()).select_from(schema.tables["pa_cycles"])) == 0


async def test_paused_card_cannot_resume_from_old_willingness(goal_api):
    _, db, _ = goal_api
    willing, _, card = await open_primary(db)
    stopped = await message(db, "我不想再细化这个目标了。")
    result = await invoke(tools(db, stopped.id), "pause_goal_card",
        source_message_id=stopped.id, source_quote=stopped.content)
    assert result["status"] == "goal_card_paused", result
    unrelated = await message(db, "最近天气真不错。")
    stale = await invoke(tools(db, unrelated.id), "open_goal_card", kind="primary",
        source_message_id=willing.id, source_quote=willing.content)
    assert stale["status"] == "blocked", stale
    await db.rollback()
    assert (await read_card_row(db, 1, "a"))["phase"] == "paused"
    resumed = await message(db, "我想继续刚才的散步目标，打开卡片吧。")
    result = await invoke(tools(db, resumed.id), "open_goal_card", kind="primary",
        source_message_id=resumed.id, source_quote=resumed.content)
    assert result["status"] == "goal_card_opened", result
    assert result["goal_card"]["id"] == card["id"]
    assert result["goal_card"]["phase"] == "formulating"


async def test_finished_secondary_cannot_create_another_from_old_extra_intent(goal_api):
    client, db, _ = goal_api
    willing, _, card = await open_primary(db, "我保留原目标，另外想骑车。", kind="secondary")
    _, executor, card = await submit_form(client, db, card, {"activity_content": "骑车"})
    checked = await review(executor, card, near_term=None, manageable=None)
    presented = await invoke(executor, "present_secondary_goal_card", card_id=card["id"],
        card_revision=checked["goal_card"]["revision"])
    await persist_display(db, presented["display_text"])
    user = await message(db, "确认，就按这个计划试试。")
    result = await invoke(tools(db, user.id), "confirm_secondary_goal_card", card_id=card["id"],
        card_revision=presented["goal_card"]["revision"])
    assert result["status"] == "secondary_confirmed", result
    unrelated = await message(db, "最近天气不错。")
    result = await invoke(tools(db, unrelated.id), "open_goal_card", kind="secondary",
        source_message_id=willing.id, source_quote=willing.content)
    assert result["status"] == "blocked", result
    await db.rollback()
    assert await db.scalar(select(func.count()).select_from(schema.tables["goal_card_workspaces"])) == 1
    willing_again = await message(db, "我还想新增一项游泳，也作为额外活动。")
    result = await invoke(tools(db, willing_again.id), "open_goal_card", kind="secondary",
        source_message_id=willing_again.id, source_quote=willing_again.content)
    assert result["status"] == "goal_card_opened", result
    assert result["goal_card"]["id"] != card["id"]


async def test_goal_ui_flag_alone_defers_tools_and_keeps_database_prompt(goal_api, context):
    from dataclasses import replace
    from types import SimpleNamespace
    from app.graph.nodes import make_module_node, ModuleConfig, route_next_module_node, _run_background_routing
    from app.models import PromptOverride
    from app.providers.base import StreamDelta, as_text

    _, db, _ = goal_api
    runtime = schema.tables["conversation_runtime_states"]
    await db.execute(update(runtime).where(runtime.c.conversation_id == 1).values(current_module="module_2"))
    db.add_all([PromptOverride(prompt_key="global", content="DB GLOBAL: 尊重用户的自主选择。", updated_by="synthetic-admin"),
        PromptOverride(prompt_key="module_2", content="DB M2: 先介绍PA、探索意愿，必要时进行价值探索。", updated_by="synthetic-admin")])
    user = await message(db, "我愿意试试散步，开始定个目标吧。")

    class Provider:
        name = "synthetic"
        model = "no-network"

        def __init__(self):
            self.requests = []

        async def stream(self, **kwargs):
            self.requests.append(kwargs)
            assert set(kwargs) == {"system", "messages"}
            system = as_text(kwargs["system"])
            assert "DB GLOBAL: 尊重用户的自主选择。" in system
            assert "DB M2: 先介绍PA、探索意愿，必要时进行价值探索。" in system
            assert "独立后台处理" in system
            yield StreamDelta(kind="content", text="可以，我们一起把你想到的散步安排细化。")

        async def stream_tools(self, **kwargs):
            pytest.fail("foreground model must not call card tools")
            yield

    provider = Provider()
    ctx = replace(context, provider=provider, router_provider=None,
        sessionmaker=async_sessionmaker(db.bind, expire_on_commit=False),
        settings=context.settings.model_copy(update={"database_schema_version": "v2",
            "pa_card_tools_enabled": False, "goal_card_ui_enabled": True, "knowledge_mediator_enabled": False}),
        prompt_snapshot=None, stream=True)
    state = {"session_id": "chat-a", "subject_id": "a", "user_message_id": user.id,
        "user_input": user.content, "memory": {}, "extracted_intent": "module_2"}
    result = await make_module_node("module_2", ModuleConfig(retrieve=False))(
        state, SimpleNamespace(context=ctx), writer=lambda event: None)
    assert result.get("error") is None, result
    assert result["pa_background_pending"] and not result.get("pa_tools_used")
    assert len(provider.requests) == 1
    assert result["final_response"] == "可以，我们一起把你想到的散步安排细化。"
    routing = await route_next_module_node({**state, **result}, SimpleNamespace(context=ctx), writer=lambda event: None)
    assert not routing["routing_pending"] and routing["next_module"] == "module_2"
    await _run_background_routing({**state, **result}, ctx, assistant_message_id=999)
    await db.rollback()
    card = await read_card_row(db, 1, "a")
    assert card is None
    assert await db.scalar(select(func.count()).select_from(schema.tables["pa_cycles"])) == 0
