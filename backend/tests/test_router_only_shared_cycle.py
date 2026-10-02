"""Business changes in a normal chat cannot replace another Router-only stage."""
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select, update

from app.database_v2_schema import metadata as schema
from app.models import AccountSettings, Conversation, UserAccount
from app.turn_confirmation import apply_pre_reply_decision
from test_goal_overview import goal_api
from test_turn_confirmation_0924 import setup_turn


async def add_shared_chats(db, *, role="admin"):
    connection = await db.connection()
    for model in (UserAccount, AccountSettings):
        await connection.run_sync(lambda conn, table=model.__table__: table.create(conn, checkfirst=True))
    await db.execute(insert(UserAccount), {"id": 1, "username": "shared-admin", "password_hash": "unused", "profile_uuid": "a"})
    await db.execute(insert(AccountSettings), {"account_id": 1, "role": role})
    await db.execute(insert(Conversation), {"id": 4, "session_id": "normal-peer", "subject_id": "a", "revision": 8})
    rt = schema.tables["conversation_runtime_states"]
    await db.execute(insert(rt), {"conversation_id": 4, "current_module": "module_3", "flow_status": "waiting_execution",
        "active_goal_id": "g1", "active_cycle_id": "m2-cycle", "row_version": 4,
        "memory": {"routing_mode": "router_code", "dialogue_draft": {"old": True}, "own_note": "normal"}})
    await db.execute(update(rt).where(rt.c.conversation_id == 2).values(
        current_module="module_1", flow_status="active", active_goal_id="g1", active_cycle_id="m2-cycle", row_version=6,
        memory={"routing_mode": "router_only", "fresh_m1": True, "dialogue_draft": {"old": True}, "own_note": "pure"}))
    # Even an invalid cross-owner cycle binding must not broaden the broadcast.
    await db.execute(update(rt).where(rt.c.conversation_id == 3).values(
        current_module="module_2", flow_status="active", active_goal_id="g1", active_cycle_id="m2-cycle", row_version=3,
        memory={"routing_mode": "router_code", "own_note": "private"}))
    await db.commit()


async def snapshots(db):
    rt = schema.tables["conversation_runtime_states"]
    rows = (await db.execute(select(rt, Conversation.revision).join(
        Conversation, Conversation.id == rt.c.conversation_id).where(rt.c.conversation_id.in_([2, 3, 4])))).mappings().all()
    return {row["conversation_id"]: dict(row) for row in rows}


@pytest.mark.parametrize("scenario,source_module,target,normal_module,normal_flow", [
    ("confirm", "module_2", "module_3", "module_3", "active"),
    ("execution", "module_3", "module_4", "module_4", "active"),
    ("edit", "module_3", "module_2", "module_3", "completed"),
])
async def test_shared_cycle_only_updates_normal_owned_chats(goal_api, scenario, source_module, target, normal_module, normal_flow):
    _, db, _ = goal_api
    text = {"confirm": "确认，就按这个计划试试。", "execution": "刚才散步完成了。", "edit": "我还没执行，想先改这个计划。"}[scenario]
    state, context = await setup_turn(db, text, module=source_module)
    await add_shared_chats(db)
    before = await snapshots(db)
    await db.commit()
    result = await apply_pre_reply_decision(state, context, SimpleNamespace(target_module=target))
    after = await snapshots(db)
    assert result["current_module"] == target
    assert after[2] == before[2]  # Mode, module, flow, memory, versions and revision all stay Router-owned.
    assert after[3] == before[3]  # Another account never receives the broadcast.
    assert after[4]["current_module"] == normal_module and after[4]["flow_status"] == normal_flow
    assert after[4]["row_version"] == before[4]["row_version"] + 1
    assert after[4]["revision"] == before[4]["revision"] + 1
    assert after[4]["memory"]["own_note"] == "normal"
    if scenario == "confirm":
        plans = schema.tables["module_two_record"]
        assert await db.scalar(select(plans.c.record_status).where(plans.c.id == "m2-draft")) == "confirmed"
    else:
        cycles = schema.tables["pa_cycles"]
        assert await db.scalar(select(cycles.c.status).where(cycles.c.id == "m2-cycle")) == (
            "reviewing" if scenario == "execution" else "cancelled")


@pytest.mark.parametrize("scenario,target", [("confirm", "module_3"), ("execution", "module_4")])
async def test_demoted_admin_uses_member_router_only_policy(goal_api, scenario, target):
    _, db, _ = goal_api
    state, context = await setup_turn(db,
        "确认，就按这个计划试试。" if scenario == "confirm" else "刚才散步完成了。",
        module="module_2" if scenario == "confirm" else "module_3")
    await add_shared_chats(db, role="user")
    before = await snapshots(db)
    await db.commit()
    await apply_pre_reply_decision(state, context, SimpleNamespace(target_module=target))
    after = await snapshots(db)
    assert after[2] == before[2]  # Business confirmation cannot reroute a member peer.
    assert after[3] == before[3]
