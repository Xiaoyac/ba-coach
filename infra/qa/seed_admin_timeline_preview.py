"""Add synthetic timeline examples to the existing isolated localadmin only.

No environment configuration, network connection, schema changes or deletion.
Repeat runs skip existing examples. Never included in the production package.
"""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from sqlalchemy import event, insert, select, update
from sqlalchemy.ext.asyncio import create_async_engine
from app.database_v2_schema import metadata as schema
from app.models import UserAccount, AccountSettings, Conversation, ConversationMessage

DB = ROOT / '.test-tmp' / 'ui-preview-0917.db'
UID = 'local-demo-2'
EXAMPLES = [
    ('park', '周末去公园，看看夏天的树影', '2026-08-02', 'completed', 'secondary', None, '去附近公园慢走，累了就在树荫下坐一会儿', '8 月 3 日下午 · 单次', 20),
    ('stand', '早晨的八分钟站桩', '2026-08-12', 'paused', 'primary', '让早晨更从容，逐渐恢复身体活力', '早餐前在家站桩，留意呼吸与身体感受', '每周五天 · 早餐前', 8),
    ('walk', '晚饭后，给自己十分钟散步', '2026-08-25', 'active', 'primary', '建立稳定、轻松的身体活动习惯', '晚饭后到小区走一圈，以舒服的速度走十分钟', '每周五天 · 晚饭后', 10),
    ('swim', '和朋友一起试一次游泳', '2026-09-04', 'completed', 'secondary', None, '和朋友去泳池，先尝试轻松游几个来回', '9 月 6 日下午 · 单次', 30),
    ('stretch', '久坐之后，给肩颈一点空间', '2026-09-10', 'active', 'secondary', None, '工作间隙起身走动，轻轻活动肩颈', '工作日下午 · 每天一次', 5),
    ('badminton', '找个周末，试试打羽毛球', '2026-09-17', 'draft', 'secondary', None, '还在讨论时间和场地，先保留这个想法', None, None),
]

async def main():
    if not DB.is_file() or DB.resolve().parent != (ROOT / '.test-tmp').resolve():
        raise RuntimeError('Expected existing isolated preview database only')
    engine = create_async_engine('sqlite+aiosqlite:///' + DB.as_posix())
    @event.listens_for(engine.sync_engine, 'connect')
    def foreign_keys(conn, _):
        conn.execute('PRAGMA foreign_keys=ON')
    added = []
    try:
        async with engine.begin() as conn:
            owner = (await conn.execute(select(UserAccount.profile_uuid, AccountSettings.role)
                .join(AccountSettings, AccountSettings.account_id == UserAccount.id)
                .where(UserAccount.username == 'localadmin'))).one()
            if owner.profile_uuid != UID or owner.role != 'admin':
                raise RuntimeError('Not the expected local demo administrator')
            goals = schema.tables['pa_goals']
            ids = ['demo-timeline-' + item[0] for item in EXAMPLES]
            existing = dict((await conn.execute(select(goals.c.id, goals.c.user_id)
                .where(goals.c.id.in_(ids)))).all())
            if any(user != UID for user in existing.values()):
                raise RuntimeError('Demo ID belongs to another user; refusing to overwrite')
            if len(existing) == len(EXAMPLES):
                print('All six examples already exist; no changes.')
                return
            # Give fixture-only confirmations their own clearly labelled source,
            # rather than referring to a real user's unrelated conversation.
            session = 'local-admin-timeline-fixture'
            cid = await conn.scalar(select(Conversation.id).where(Conversation.session_id == session))
            if cid is None:
                result = await conn.execute(insert(Conversation).values(subject_id=UID,
                    session_id=session, title='目标时间线演示 · 合成资料', revision=1,
                    created_at=datetime(2026,8,1), updated_at=datetime(2026,8,1)))
                cid = result.inserted_primary_key[0]
                result = await conn.execute(insert(ConversationMessage).values(conversation_id=cid,
                    position=0, role='user', content='【本地合成演示资料】这些目标、计划确认和复盘均用于界面预览，不代表真实用户经历或承诺。'))
                mid = result.inserted_primary_key[0]
            else:
                if await conn.scalar(select(Conversation.subject_id).where(Conversation.id==cid)) != UID:
                    raise RuntimeError('Fixture conversation ownership mismatch')
                mid = await conn.scalar(select(ConversationMessage.id).where(ConversationMessage.conversation_id==cid).order_by(ConversationMessage.position).limit(1))
                if mid is None:
                    raise RuntimeError('Fixture source message missing')
            async def add(table, **values):
                await conn.execute(insert(schema.tables[table]).values(**values))
            for key, title, day, state, kind, direction, content, schedule, minutes in EXAMPLES:
                gid = 'demo-timeline-' + key
                if gid in existing:
                    continue
                stamp = datetime.fromisoformat(day) + timedelta(hours=1)
                ended = stamp + timedelta(days=2) if state=='completed' else None
                await add('pa_goals', id=gid,user_id=UID,title=title,status=state,
                    status_reason='本地界面演示数据', created_from_conversation_id=cid,
                    created_at=stamp,updated_at=stamp,closed_at=ended)
                await add('pa_goal_details',goal_id=gid,goal_kind=kind,long_term_direction=direction,
                    source_conversation_id=cid,source_message_id=mid,evidence={'synthetic':True},created_at=stamp,updated_at=stamp)
                versions = 2 if key=='walk' else 1
                for version in range(1,versions+1):
                    pid = f'{gid}-p{version}'
                    pdate = stamp + timedelta(days=10 if version==2 else 0)
                    await add('module_two_record',id=pid,goal_id=gid,version_no=version,
                        record_status='draft' if state=='draft' else 'superseded' if version<versions else 'confirmed',
                        confirmation_status='unconfirmed' if state=='draft' else 'confirmed',
                        confirmation_message_id=None if state=='draft' else mid,
                        activity_content='晚饭后到小区走一圈，以舒服的速度走二十分钟' if key=='walk' and version==1 else content,
                        schedule_text=schedule,timezone='Asia/Shanghai',
                        duration_minutes=20 if key=='walk' and version==1 else minutes,
                        location='附近公园或小区' if key in ('walk','park') else '根据当天的安排选择',
                        core_values=['照顾自己','保留弹性'],companion='可以独自进行，也可以邀请朋友',
                        potential_barriers=['疲惫','天气不合适'],barrier_coping_plan=[{'barrier':'疲惫','plan':'缩短一点，不追求一次做很多'}],
                        created_at=pdate,updated_at=pdate)
                    await add('pa_plan_details',plan_id=pid,schedule_kind='one_off' if key in ('park','swim','badminton') else 'recurring',
                        review_cadence='活动后聊聊感受' if key in ('park','swim','badminton') else '每周回顾一次',
                        difficulty='以舒服、做得到为准',resources=['舒服的衣服','可自由调整的时间'],created_at=pdate,updated_at=pdate)
                if state!='draft':
                    await conn.execute(update(goals).where(goals.c.id==gid).values(current_plan_record_id=f'{gid}-p{versions}'))
                    rounds = 3 if key=='walk' else 1
                    for n in range(1,rounds+1):
                        cycle_id = f'{gid}-c{n}'
                        cdate=stamp+timedelta(days=(n-1)*7)
                        done = n<rounds or state in ('completed','paused')
                        finish = cdate+timedelta(days=2 if state=='completed' else 6) if done else None
                        await add('pa_cycles',id=cycle_id,goal_id=gid,ordinal=n,
                            module_two_record_id=f'{gid}-p{2 if key=="walk" and n==3 else 1}',
                            status='completed' if done else 'waiting_execution',
                            started_from_conversation_id=cid,created_at=cdate,updated_at=finish or cdate,
                            started_at=cdate if done else None,completed_at=finish)
                        await add('pa_cycle_progress',cycle_id=cycle_id,module_2_steps=[1,2,3,4],module_3_steps=[1,2,3],module_4_steps=[])
                        if done:
                            summary = '这次活动已经完成，给自己留下一段轻松的体验。' if state=='completed' else '这段时间有做一些尝试，也有没做到的日子。先保留经验，按自己的节奏调整。'
                            await add('module_four_record',id=f'{cycle_id}-r',cycle_id=cycle_id,
                                record_status='draft',review_summary='【演示复盘】'+summary,created_at=finish,updated_at=finish)
                            await add('pa_review_details',review_id=f'{cycle_id}-r',action='pause' if state=='paused' else 'end' if state=='completed' else 'continue',
                                source_message_id=mid,source_quote='本地合成演示资料',created_at=finish,updated_at=finish)
                added.append({'title':title,'created':day,'status':state})
            print({'local_account':'localadmin','added':added,'existing_data_overwritten':False})
    finally:
        await engine.dispose()

if __name__=='__main__':
    asyncio.run(main())
