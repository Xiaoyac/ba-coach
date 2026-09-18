"""Synthetic local-only UI preview. Never reads environment database credentials.

Run with backend/.venv/Scripts/python.exe infra/qa/seed_local_ui_preview.py.
Refuses to overwrite the preview database; no production connection is possible.
"""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import insert, update, event
from sqlalchemy.ext.asyncio import create_async_engine
from app.db import Base
from app import models
from app.database_v2_schema import metadata as schema
from app.security import hash_password

DB = ROOT / ".test-tmp" / "ui-preview-0917.db"

async def main():
    assert DB.resolve().parent == (ROOT / ".test-tmp").resolve()
    if DB.exists():
        print("Preview already exists; no data overwritten:", DB)
        return
    DB.parent.mkdir(exist_ok=True)
    engine = create_async_engine("sqlite+aiosqlite:///" + DB.as_posix())
    @event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
    now = datetime.now()
    async with engine.begin() as c:
        await c.run_sync(schema.create_all)
        await c.run_sync(Base.metadata.create_all)
        async def add(name, **values):
            await c.execute(insert(schema.tables[name]), values)
        for account_id, username, role in [(1, "localpreview", "user"), (2, "localadmin", "admin"), (3, "localempty", "user")]:
            uid = f"local-demo-{account_id}"
            await add("user_profile", uuid=uid, nickname="本地体验" if account_id == 1 else "本地管理体验" if account_id == 2 else "空档案体验", module1_done_flag=True)
            await add("user_module_one_state", user_id=uid, completed_steps=[], status="completed", completion_source="legacy_imported", evidence_status="missing")
            await c.execute(insert(models.UserAccount), dict(id=account_id, username=username, password_hash=hash_password("LocalPreview0917!"), profile_uuid=uid))
            await c.execute(insert(models.AccountSettings), dict(account_id=account_id, role=role))
            await c.execute(insert(models.AccountHandle), dict(account_id=account_id, base_username=username, normalized_base=username, tag="09170"))
            await c.execute(insert(models.AccountEmail), dict(account_id=account_id, email=f"{username}@example.com", normalized_email=f"{username}@example.com", verified_at=now))
        uid = "local-demo-1"
        await c.execute(insert(models.Conversation), dict(id=1, subject_id=uid, session_id="local-preview-chat", title="给自己一点散步的时间 · 演示", revision=1))
        transcript = [
            ("assistant", "这里是本地界面体验，以下对话和目标都是演示资料，不会同步到线上。\n\n今天想从哪里聊起？"),
            ("user", "这周我试了晚饭后出去走走。不是每天都做到了，但走完以后，心情好像轻松了一点。"),
            ("assistant", "没有每天都做到，也不妨碍这几次尝试有意义。你已经发现了一个属于自己的小变化：**走完以后，心情轻松了一点。**\n\n我们不用急着把计划变得更多。可以先保留晚饭后散步的安排，累的时候缩短一点，让它更贴近你现在的生活。\n\n你觉得这周走起来最舒服的一次，是什么时候？"),
            ("user", "周三那次，刚好夕阳很好。我想继续每周五天，每次先走十分钟。"),
            ("assistant", "那就先留住这个舒服的节奏：晚饭后，走十分钟；一周试着安排五天。遇到下雨或特别累的日子，也可以再商量。\n\n之前讨论过的安排和每次复盘，都可以在左侧的「我的目标」里回顾，不需要重新填表。"),
        ]
        for n, (role, content) in enumerate(transcript):
            await c.execute(insert(models.ConversationMessage), dict(id=n+1, conversation_id=1, position=n, role=role, content=content))
        goals = [
            ("walk", "晚饭后，给自己十分钟散步", "active", "primary", "建立更轻松、稳定的身体活动习惯", "晚饭后到附近公园走一小圈", "每周五天 · 晚饭后", 10),
            ("stretch", "工作间隙的舒展", "active", "secondary", None, "起身走动，轻轻活动肩颈", "工作日下午 · 想起来时", 5),
            ("park", "周末去公园看看", "completed", "secondary", None, "在树荫下慢慢走走，坐一会儿", "上周六下午 · 单次", 20),
            ("stand", "晨间站桩练习", "paused", "primary", "让早晨更从容", "在家安静站桩，留意身体感受", "每周五天 · 早餐前", 8),
            ("swim", "试试游泳", "draft", "unclassified", None, "还在讨论适合自己的方式", None, None),
            ("run", "换一种更适合的活动", "replaced", "secondary", None, "原先的慢跑安排，已改为散步", "每周两次", 15),
        ]
        for idx, (gid, title, status, kind, direction, content, schedule, duration) in enumerate(goals):
            stamp = now - timedelta(days=idx)
            await add("pa_goals", id=gid, user_id=uid, title=title, status=status, created_at=stamp-timedelta(days=40), updated_at=stamp, created_from_conversation_id=1)
            await add("pa_goal_details", goal_id=gid, goal_kind=kind, long_term_direction=direction)
            versions = 2 if gid == "walk" else 1
            for version in range(1, versions+1):
                pid = f"{gid}-p{version}"
                plan_status = "draft" if status == "draft" else "superseded" if version < versions else "confirmed"
                await add("module_two_record", id=pid, goal_id=gid, version_no=version, record_status=plan_status,
                    confirmation_status="unconfirmed" if status == "draft" else "confirmed", confirmation_message_id=4,
                    activity_content=content, schedule_text=schedule, timezone="Asia/Shanghai",
                    duration_minutes=20 if gid == "walk" and version == 1 else duration, location="附近公园" if gid == "walk" else "根据当天情况选择",
                    frequency_rule={"days_per_week":5} if gid == "walk" else None,
                    core_values=["照顾自己", "保留生活的节奏"], potential_barriers=["下雨", "下班后觉得累"],
                    barrier_coping_plan=[{"barrier":"下雨", "plan":"在家走动几分钟，也可以休息"}], companion="可以独自进行",
                    created_at=stamp-timedelta(days=30-version), updated_at=stamp)
                await add("pa_plan_details", plan_id=pid, schedule_kind="one_off" if kind == "secondary" else "recurring", review_cadence="每周回顾一次", difficulty="从不费力的小尝试开始", resources=["舒适的鞋子", "家人理解"])
            if status != "draft":
                await c.execute(update(schema.tables["pa_goals"]).where(schema.tables["pa_goals"].c.id == gid).values(current_plan_record_id=f"{gid}-p{versions}"))
        for n in range(1, 15):
            cid = f"walk-c{n}"
            stamp = now-timedelta(days=(14-n)*7)
            await add("pa_cycles", id=cid, goal_id="walk", ordinal=n, status="waiting_execution" if n==14 else "completed", module_two_record_id="walk-p1" if n<8 else "walk-p2", created_at=stamp, started_at=stamp, completed_at=None if n==14 else stamp+timedelta(days=6))
            if n < 14:
                await add("module_four_record", id=f"review-{n}", cycle_id=cid, record_status="confirmed", execution_result=4, review_decision=1, confirmation_message_id=4, review_summary="有几天尝试了散步，感觉轻松一点。下周继续保留弹性。")
                await add("pa_review_details", review_id=f"review-{n}", action="continue", source_message_id=4, source_quote="本地演示数据")
        for gid, status in [("stretch", "planning"), ("stand", "completed"), ("park", "completed"), ("run", "completed")]:
            cid = f"{gid}-c1"
            await add("pa_cycles", id=cid, goal_id=gid, ordinal=1, status=status, module_two_record_id=f"{gid}-p1", completed_at=now if status=="completed" else None)
            await add("pa_cycle_progress", cycle_id=cid, module_2_steps=[], module_3_steps=[], module_4_steps=[])
            if status == "completed":
                await add("module_four_record", id=f"{gid}-review", cycle_id=cid, record_status="confirmed", execution_result=4, review_decision=4, confirmation_message_id=4, review_summary="先保留这次经验，按自己的节奏决定下一步。")
                await add("pa_review_details", review_id=f"{gid}-review", action="pause" if gid=="stand" else "end", source_message_id=4, source_quote="本地演示数据")
        await add("conversation_runtime_states", conversation_id=1, current_module="module_4", flow_status="waiting_execution", active_goal_id="walk", active_cycle_id="walk-c14", memory={})
        await add("pa_cycle_progress", cycle_id="walk-c14", module_2_steps=[1,2,3,4], module_3_steps=[1,2,3], module_4_steps=[])
        for n, event_status in [(1,"superseded"),(2,"active")]:
            await add("pa_activity_events", id=f"walk-event{n}", user_id=uid, goal_id="walk", cycle_id="walk-c13", source_conversation_id=1, source_message_id=2, event_index=n, event_kind="performed", status=event_status, activity_content="周三散步" if n==1 else "周三散步十分钟", occurred_at_text="上周三傍晚", effect="走完心情轻松了一点", source_quote="本地演示数据")
        await add("pa_activity_events", id="loose-idea", user_id=uid, source_conversation_id=1, source_message_id=4, event_index=3, event_kind="idea", activity_content="天气好时去看看花", source_quote="本地演示数据")
    await engine.dispose()
    print("Created isolated synthetic preview:", DB)
    print("Accounts: localpreview / localadmin / localempty; password: LocalPreview0917!")

if __name__ == "__main__":
    asyncio.run(main())
