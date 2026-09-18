"""Evidence-scoped goal classification and activity capture, not LLM authority."""
import re
from sqlalchemy import select, insert, update, delete
from .clinical_fields import Spec
from .database_v2_schema import metadata as schema
from .models import Conversation, ConversationMessage
from .v2_repository import new_id, now

GOAL_SPEC = Spec("goal_proposal", "json", """仅在用户已明确选择要尝试的活动时提取对象，否则 null。
对象字段：goal_kind(primary主要目标/secondary次要目标)，long_term_direction(用户希望长期改善的方向，次要可null)，
selection_quote(本轮用户确认选择的逐字原话)，activity_quote(具体活动的原文短语，必须出现在用户原话或紧邻该用户回复前的助手建议中，且包含在target_activity_content中)，
direction_quote(希望改变的方向的用户逐字原话，主要目标必需)。
主要目标是用户长期方向下选出的活动；次要目标可独立存在，不要求有主要目标，不等于一次性。
不要把尚在考虑的想法、助手单方建议、已经做过的临时活动当成新目标。不要要求用户说出主要/次要术语。
本轮纠正优先；含糊意愿或尚未选择时一律null。""")
PLAN_SPEC = Spec("plan_context", "json", """对象：schedule_kind(recurring周期性/one_off一次性/unspecified未知)，
schedule_quote(频率的用户逐字原话)，review_cadence(复盘节奏的用户原话或null)，difficulty(活动难度的用户原话或null)，
resources(用户明确提到的可用资源原话数组或null)。执行频率、复盘节奏、目标持续时长独立，不能把每周五天解释成每五天复盘。""")
ACTIVITY_SPEC = Spec("activity_observations", "json", """只提取最后一条用户消息中自述的活动，最多8项，没提到则[]。
每项：event_kind(performed已做/not_performed明确没做/idea未来想法)，activity_content(原话中的活动名称)，
source_quote(最后用户消息中包含事件完整含义的逐字原话)，occurred_at_text(时间原话或null)，effect(感受效果原话或null)。
不能把未来想法记为已做；不能把没做记为已做；同一活动同一事件不重复拆分。
“没站桩，但散步了感觉轻松”拆成站桩not_performed和散步performed两个事件。
“又站桩了”只是一次执行记录，不是新目标；“游泳可能不错”只是idea。
不要给出goal_id/cycle_id，归属由程序验证；没有明确活动名称时不猜测。""")

REVIEW_SPEC = Spec("review_followup", "json", """本轮用户明确复盘下一步决定时的对象，否则null。
action: continue(继续原计划，review_decision=1)，adjust(调整原计划，review_decision=3)，pause(暂停，review_decision=4)，end(结束，review_decision=4)，replace_keep(更换焦点但保留旧目标，review_decision=2)，
replace_pause(更换并明确暂停旧目标，review_decision=2)。source_quote是本轮用户逐字原话。
用户只说换目标但没说暂停旧目标时用replace_keep，不能默认替他暂停。
日常没做活动、休息一天不等于暂停目标；不明确时继续讨论。""")
CORRECTION_SPEC = Spec("activity_corrections", "json", """用户本轮明确更正之前的活动自述时才给数组，否则[]。
每项prior_quote是之前用户活动自述的逐字原话（可用完整句子），source_quote是本轮更正的逐字原话。
必须是对之前事实的更正，不是又发生了一次事件；'今天没做'不能撤销昨天做过。最多4项，不能猜测对应关系。
纠正后的新事实同时放入activity_observations，不要把旧事实也放进去。""")

GOAL_RUNTIME_CONTRACT = """【目标与活动契约，优先于与其冲突的旧流程描述】
M2通过自然聊天了解PA、用户希望的改变或意向、选择活动、难度、障碍资源及应对、具体计划。
主要目标服务于用户明确的长期方向；次要目标可独立存在，不能强制挂在主要目标下。
主要/次要与周期性/一次性是两个维度，不要让用户填分类表或强制做价值观练习。
不把助手建议、模糊想法、用户已经做过的临时活动自动转成目标。明确选择后才由Agent创建草稿。
每段聊天只有一个焦点目标；提到其他活动仍可倾听记录，不得偷偷换绑目标。
新目标请引导用户新建聊天选“讨论新目标”，无需事先输入名称。
M4每次日常反馈可以只记录，不必强迫完整复盘。没做原活动但做了另一件事须分开，不能算原目标完成。
确认继续原计划会进入同一目标下一周期M4，无需重做M2/M3；调整进入同一目标M2新版本。
暂停保留目标和历史，不要求新建目标；恢复可在新聊天明确选择暂停目标。更换目标不自动暂停旧目标，需用户明确决定。
复盘周期结束不等于目标结束。频率、复盘节奏、目标寿命独立。不宣称未经后台确认的保存或切换已成功。
活动后提醒须由用户在网页“活动后提醒”设置中主动开启并授权；聊天中的口头同意不代替浏览器授权。
只有明确日期/周期、开始时间和时长的已确认计划才可能在预计结束15分钟后提醒；不要保证已设置或准时送达，不为提醒强迫用户补齐计划。
"""


def text(value, limit=1000):
    return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= limit else None


async def evidence_messages(db, conversation_id, user_id):
    return (await db.execute(select(ConversationMessage).join(Conversation).where(
        Conversation.id == conversation_id, Conversation.subject_id == user_id)
        .order_by(ConversationMessage.position))).scalars().all()


def proposal_evidence(raw, messages, activity):
    if not isinstance(raw, dict):
        return None
    users = [m for m in messages if m.role == "user"]
    if not users:
        return None
    latest = users[-1]
    selection = text(raw.get("selection_quote"), 2000)
    phrase = text(raw.get("activity_quote"), 255)
    kind = raw.get("goal_kind")
    if kind not in {"primary", "secondary"} or not selection or selection not in latest.content:
        return None
    if re.search(r"可能|也许|考虑|说不定|不确定|没想好|不想|不打算|先不|不要|还没决定", selection):
        return None
    previous = next((m for m in reversed(messages) if m.position < latest.position), None)
    if not phrase or phrase not in activity or not (phrase in latest.content or
        (previous and previous.role == "assistant" and phrase in previous.content)):
        return None
    if re.search(r"(?:不想|不打算|不要|先不|没选).{0,6}" + re.escape(phrase), latest.content):
        return None
    direction = text(raw.get("long_term_direction"))
    direction_quote = text(raw.get("direction_quote"), 2000)
    if kind == "primary" and (not direction or not direction_quote or
        not any(direction_quote in m.content for m in users) or direction not in direction_quote):
        return None
    return {"goal_kind": kind, "long_term_direction": direction if kind == "primary" else None,
        "source_message_id": latest.id, "source_conversation_id": latest.conversation_id,
        "evidence": {"selection_quote": selection, "activity_quote": phrase, "direction_quote": direction_quote}}


async def save_goal_details(db, goal_id, evidence):
    table = schema.tables["pa_goal_details"]
    exists = (await db.execute(select(table.c.goal_id).where(table.c.goal_id == goal_id))).scalar_one_or_none()
    if exists:
        await db.execute(update(table).where(table.c.goal_id == goal_id).values(**evidence, updated_at=now()))
    else:
        await db.execute(insert(table), {"goal_id": goal_id, **evidence})


async def save_plan_context(db, plan_id, raw, messages):
    if not isinstance(raw, dict):
        return
    user_texts = [m.content for m in messages if m.role == "user"]
    def quoted(value, limit):
        value = text(value, limit)
        return value if value and any(value in body for body in user_texts) else None
    schedule_quote = quoted(raw.get("schedule_quote"), 2000)
    kind = raw.get("schedule_kind")
    values = {"schedule_kind": kind if schedule_quote and kind in {"recurring", "one_off"} else "unspecified",
        "review_cadence": quoted(raw.get("review_cadence"), 255),
        "difficulty": quoted(raw.get("difficulty"), 255),
        "resources": [v for item in raw.get("resources", [])[:12] if (v := quoted(item, 255))]
            if isinstance(raw.get("resources"), list) else None}
    table = schema.tables["pa_plan_details"]
    exists = (await db.execute(select(table.c.plan_id).where(table.c.plan_id == plan_id))).scalar_one_or_none()
    if exists:
        await db.execute(update(table).where(table.c.plan_id == plan_id).values(**values, updated_at=now()))
    else:
        await db.execute(insert(table), {"plan_id": plan_id, **values})


async def capture_activities(db, *, user_id, conversation, state, raw, messages, corrections=None):
    if (not isinstance(raw, list) or state["flow_status"] in {"completed", "paused"}
            or conversation.subject_id != user_id or state["conversation_id"] != conversation.id):
        return
    users = [m for m in messages if m.role == "user"]
    if not users:
        return
    latest = users[-1]
    if latest.conversation_id != conversation.id:
        return
    table = schema.tables["pa_activity_events"]
    await db.execute(update(table).where(table.c.user_id == user_id,
        table.c.source_conversation_id == conversation.id, table.c.superseded_by_message_id == latest.id).values(
        status="active", superseded_by_message_id=None))
    if isinstance(corrections, list):
        for correction in corrections[:4]:
            if not isinstance(correction, dict):
                continue
            prior, quote = text(correction.get("prior_quote"), 2000), text(correction.get("source_quote"), 2000)
            if not prior or not quote or quote not in latest.content or not re.search(r"更正|说错|纠正|记错|不是.+而是", quote):
                continue
            candidates = (await db.execute(select(table.c.id, table.c.source_quote).where(
                table.c.user_id == user_id, table.c.source_conversation_id == conversation.id,
                table.c.source_message_id != latest.id, table.c.status == "active"))).mappings().all()
            matched = [r["id"] for r in candidates if r["source_quote"] == prior]
            if len(matched) == 1:
                await db.execute(update(table).where(table.c.id == matched[0]).values(
                    status="superseded", superseded_by_message_id=latest.id))
    # A repeated extraction is one full snapshot of this source message only.
    await db.execute(delete(table).where(table.c.user_id == user_id, table.c.source_message_id == latest.id))
    goals, plans = schema.tables["pa_goals"], schema.tables["module_two_record"]
    goal = (await db.execute(select(goals.c.id, goals.c.title, plans.c.activity_content).outerjoin(plans,
        (plans.c.id == goals.c.current_plan_record_id) & (plans.c.goal_id == goals.c.id)).where(
        goals.c.id == state["active_goal_id"], goals.c.user_id == user_id))).mappings().one_or_none()
    seen = set()
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        quote, activity = text(item.get("source_quote"), 2000), text(item.get("activity_content"), 255)
        kind = item.get("event_kind")
        if not quote or quote not in latest.content or not activity or activity not in quote or kind not in {"performed", "not_performed", "idea"}:
            continue
        fingerprint = (activity, kind, quote)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        # Literal match only. Ambiguous activities remain unlinked, not guessed.
        linked = goal and len(activity) >= 2 and (activity in goal["title"] or activity in (goal["activity_content"] or "")) and kind != "idea"
        def in_quote(key, limit):
            value = text(item.get(key), limit)
            return value if value and value in quote else None
        await db.execute(insert(table), {"id": new_id(), "user_id": user_id,
            "goal_id": goal["id"] if linked else None, "cycle_id": state["active_cycle_id"] if linked else None,
            "source_conversation_id": conversation.id, "source_message_id": latest.id,
            "event_index": len(seen), "event_kind": kind, "activity_content": activity,
            "source_quote": quote, "occurred_at_text": in_quote("occurred_at_text", 255), "effect": in_quote("effect", 1000)})


async def public_goal_details(db, user_id):
    details, goals = schema.tables["pa_goal_details"], schema.tables["pa_goals"]
    rows = (await db.execute(select(details).join(goals).where(goals.c.user_id == user_id))).mappings().all()
    return {row["goal_id"]: {key: row[key] for key in ("goal_kind", "long_term_direction")} for row in rows}


async def save_review_followup(db, review_id, raw, messages, decision):
    table = schema.tables["pa_review_details"]
    await db.execute(delete(table).where(table.c.review_id == review_id))
    if not isinstance(raw, dict) or not messages:
        return
    users = [m for m in messages if m.role == "user"]
    if not users:
        return
    latest = users[-1]
    action, quote = raw.get("action"), text(raw.get("source_quote"), 2000)
    allowed = {1: {"continue"}, 3: {"adjust"}, 4: {"pause", "end"}, 2: {"replace_keep", "replace_pause"}}
    if action not in allowed.get(decision, set()) or not quote or quote not in latest.content:
        return
    if action in {"pause", "replace_pause"} and not re.search(r"暂停|先停|停一阵|暂时不做|先放一放", quote):
        return
    await db.execute(insert(table), {"review_id": review_id, "action": action,
        "source_message_id": latest.id, "source_quote": quote})


async def public_activities(db, user_id, *, conversation_id=None):
    table = schema.tables["pa_activity_events"]
    query = select(table).where(table.c.user_id == user_id, table.c.status == "active")
    query = query.where(table.c.source_conversation_id == conversation_id) if conversation_id else query.where(table.c.goal_id.is_(None))
    rows = (await db.execute(query.order_by(table.c.created_at.desc(), table.c.id).limit(30))).mappings().all()
    return [{k: row[k] for k in ("id", "activity_content", "event_kind", "occurred_at_text", "effect", "goal_id")} for row in rows]
