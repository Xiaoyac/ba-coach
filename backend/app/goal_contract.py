"""Evidence-scoped goal classification and activity capture, not LLM authority."""
import re
from sqlalchemy import select, insert, update, delete
from .clinical_fields import Spec
from .database_v2_schema import metadata as schema
from .models import Conversation, ConversationMessage
from .v2_repository import new_id, now

GOAL_SPEC = Spec("goal_proposal", "json", """从完整当前对话语义识别用户仍然有效的活动选择，不能从助手建议、过去做过或考虑/也许推定选择。
对象字段：selection_status(selected真实明确选择/ambiguous含糊/not_expressed尚未表达/retracted已撤回)，
selection_message_id(输入提供的真实用户消息ID)，selection_quote(该消息中表达选择的连续原文)，
activity_quote(所选活动的连续原文，可来自该用户消息或紧邻前一条助手提议)，selection_role(core正式核心草稿/secondary额外次要活动/trial尚无生效核心目标时的临时体验)，
goal_kind(primary主要目标/secondary次要目标)，long_term_direction(明确表达的长期方向，可null)，direction_quote(方向的用户原话或null)。
selected由上下文判断，不要求用户说固定句式；回答某个活动名、自然接受单一提议也可能构成选择；用户当前犹豫、否定或撤回时不得沿用旧selected。
已有有效选择在后续讨论细节时继续引用原消息，不因新一轮没有重复活动名而丢失。尚未选定也返回状态，不能要求用户为抽取失败重复表达。
目标卡不完整时仍保存真实选择和draft；长期方向/core_values不是所有用户的必填条件。不能根据缺少方向把主要目标自动改成次要目标。
输入未提供消息ID时可省略selection_message_id，但selection_quote必须能唯一定位用户消息；不能自行编号或伪造ID。""")
M2_CONTEXT_SPEC = Spec("m2_activity_context", "json", """M2当前意愿及次要/临时体验的有源记录快照，未涉及则null。不要把次要或临时体验直接创建为核心目标。
对象字段：intention(可null，{value:no_intention/intention/action,message_id:真实用户消息ID,quote:当前意愿原话})；
trial(可null，{state:none/trial_active/trial_completed/trial_continue/trial_upgrade_to_core/trial_retained_secondary,activity_content:活动原文,source_role:trial/secondary,message_id:真实用户消息ID,quote:原话})；
secondary_activities(数组，每项{activity_content:用户主动额外提出的PA活动原文,message_id:真实用户消息ID,quote:用户原话,time/location/frequency/companion/duration:用户主动给出的信息或null})。
按批准M2 Prompt判断：生效核心目标之外新增活动是secondary，不主动推动转正。没有生效核心目标时，用户主动试水记trial_active；用户实际完成才trial_completed；用户明确选择继续一次才trial_continue；明确同意转正才trial_upgrade_to_core，之后仍须完成完整核心计划与确认；拒绝转正则trial_retained_secondary。历史做过/助手提议/犹豫不能算用户已选择。无法判断就不填写状态，不按聊天轮数伪造独立体验次数。
未提供的次要信息不追问不补全；不要自动修改活动时长频次。每项必须能对应真实用户来源，只记录已发生、仍有效的状态。""")
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

REVIEW_SPEC = Spec("review_followup", "json", """本周期当前仍有效的用户明确复盘下一步决定快照；尚未决定或已经撤回则null。
action: continue(继续原计划，review_decision=1)，adjust(调整原计划，review_decision=3)，pause(暂停，review_decision=4)，end(结束，review_decision=4)，replace_keep(更换焦点但保留旧目标，review_decision=2)，
replace_pause(更换并明确暂停旧目标，review_decision=2)。source_quote是本周期用户逐字原话，必须与m4_contract.decision_quote来自同一条用户发言、对应同一决定。
之后补充复盘信息或重复同一决定，不清除原来仍有效的决定。实质改方向时引用新决定；撤回且未重选时为null，不能重用旧决定。
source_quote中出现“改成/调整/修改/重新讨论/更换/暂停/结束”等明确方向时，action必须与该方向一致；即使目标或数值碰巧与当前计划相同，也不能归为continue。助手提出的“按原计划继续，可以吗”等只是提议，不是用户决定，不能作为decision_evidence。
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


def source_reference(raw, messages, *, role="user", id_key="message_id", quote_key="quote"):
    """Resolve an exact quote to a trusted row; never infer conversational intent."""
    if not isinstance(raw, dict):
        return None
    quote = text(raw.get(quote_key), 2000)
    if not quote:
        return None
    source_id = raw.get(id_key)
    if source_id is not None:
        if type(source_id) is not int:
            return None
        matches = [m for m in messages if m.id == source_id and m.role == role
                   and quote in (m.content or "")]
    else:
        matches = [m for m in messages if m.role == role and quote in (m.content or "")]
    return matches[0] if len(matches) == 1 else None


def proposal_evidence(raw, messages, activity):
    """Validate the extractor's semantic choice against authenticated sources.

    A legacy candidate without selection_status needs a new extraction over
    existing messages; it cannot be promoted by keyword recovery. Existing
    confirmed records are unaffected by this candidate-only check.
    """
    if (not isinstance(raw, dict) or raw.get("selection_status") != "selected"
            or raw.get("selection_role") != "core"):
        return None
    if raw.get("goal_kind") not in {"primary", "secondary"}:
        return None
    source = source_reference(raw, messages, id_key="selection_message_id", quote_key="selection_quote")
    phrase = text(raw.get("activity_quote"), 255)
    if source is None or not phrase or not isinstance(activity, str) or not activity.strip():
        return None
    previous = next((m for m in reversed(messages) if m.position < source.position), None)
    sources = [source.content or ""]
    if previous and previous.role == "assistant":
        sources.append(previous.content or "")
    if not any(phrase in content for content in sources):
        return None
    # Keep the literal activity chosen by the user. An unconfirmed later
    # assistant card cannot expand it into another activity.
    normalized = activity.strip()
    if normalized not in source.content and phrase not in normalized and normalized not in phrase:
        return None
    source_activity = normalized if normalized in source.content else phrase
    direction, direction_quote = text(raw.get("long_term_direction")), text(raw.get("direction_quote"), 2000)
    direction_valid = (direction and direction_quote and direction in direction_quote
                       and any(m.role == "user" and direction_quote in (m.content or "") for m in messages))
    return {"goal_kind": raw["goal_kind"], "long_term_direction": direction if direction_valid else None,
        "source_message_id": source.id, "source_conversation_id": source.conversation_id,
        "source_activity": source_activity,
        "evidence": {"selection_status": "selected", "selection_quote": raw["selection_quote"],
                     "selection_role": "core", "activity_quote": phrase,
                     "direction_quote": direction_quote if direction_valid else None}}


def recover_goal_proposal(messages, activity):
    """Compatibility entry point: missing semantic extraction is never guessed."""
    return None


_SCORE_WORDS = {0: ("0", "零", "〇"), 1: ("1", "一"), 2: ("2", "二", "两"),
                3: ("3", "三"), 4: ("4", "四"), 5: ("5", "五"),
                6: ("6", "六"), 7: ("7", "七"), 8: ("8", "八"),
                9: ("9", "九"), 10: ("10", "十")}


def difficulty_values(data, messages, *, existing=None):
    """Persist only user-scored numbers with a real, role-correct source.

    Intent and adjustment semantics belong to extraction. This validator
    checks the number itself, its literal source, role and ordering.
    """
    keys = ("difficulty_rating", "difficulty_original", "difficulty_evidence")
    if not any(key in data for key in keys):
        return {}
    raw = data.get("difficulty_evidence")
    raw = raw if isinstance(raw, dict) else {}
    verified = {}
    values = {"difficulty_rating": None, "difficulty_original": None, "difficulty_evidence": {}}
    positions = {}
    for kind, field in (("rating", "difficulty_rating"), ("original", "difficulty_original")):
        score = data.get(field)
        reference = raw.get(kind)
        source = source_reference(reference, messages)
        if type(score) is not int or score not in _SCORE_WORDS or source is None:
            continue
        if kind == "original" and score < 6:
            continue
        score_text = reference.get("score_text")
        quote = reference["quote"]
        if score_text not in _SCORE_WORDS[score] or score_text not in quote:
            continue
        # Numeric boundaries prevent a 4 from being borrowed from 14/40.
        if score_text.isdigit() and not re.search(r"(?<!\d)" + re.escape(score_text) + r"(?!\d)", quote):
            continue
        if not score_text.isdigit() and not re.search(
                r"(?<![零〇一二两三四五六七八九十百\d])" + re.escape(score_text)
                + r"(?![零〇一二两三四五六七八九十百\d])", quote):
            continue
        values[field] = score
        verified[kind] = {"value": score, "message_id": source.id,
                          "quote": quote, "score_text": score_text}
        positions[kind] = source.position
    if "original" in verified and ("rating" not in verified or positions["original"] >= positions["rating"]):
        values["difficulty_original"] = None
        verified.pop("original")
    # Omission does not erase a previously verified initial rating. Revalidate
    # its source before carrying it to a newer extraction of the same plan.
    old_evidence = existing.get("difficulty_evidence") if existing else None
    old_original = old_evidence.get("original") if isinstance(old_evidence, dict) else None
    if "original" not in raw and "rating" in verified and isinstance(old_original, dict):
        old_source = source_reference(old_original, messages)
        if (old_source is not None and type(old_original.get("value")) is int
                and 6 <= old_original["value"] <= 10 and old_source.position < positions["rating"]):
            values["difficulty_original"] = old_original["value"]
            verified["original"] = old_original
    values["difficulty_evidence"] = verified
    return values


def invalidate_stale_difficulty(values, existing, messages):
    """A changed, already specified plan cannot inherit the old plan's score.

    Merely adding a previously absent optional detail does not constitute
    an existing plan change here. This operates only on an editable draft.
    """
    if not existing or existing.get("record_status") != "draft":
        return values
    fields = ("activity_content", "schedule_text", "scheduled_start_at", "location",
              "duration_minutes", "frequency_rule", "companion")
    changed = [key for key in fields if key in values and existing.get(key) is not None
               and values[key] != existing[key]]
    if not changed:
        return values
    evidence = values.get("difficulty_evidence", existing.get("difficulty_evidence"))
    evidence = dict(evidence) if isinstance(evidence, dict) else {}
    rating = evidence.get("rating")
    source = source_reference(rating, messages)
    latest_user = next((m for m in reversed(messages) if m.role == "user"), None)
    if source is not None and latest_user is not None and source.position >= latest_user.position:
        return values
    if rating:
        evidence["previous_rating"] = rating
    evidence.pop("rating", None)
    evidence["invalidated_plan_fields"] = changed
    return {**values, "difficulty_rating": None, "difficulty_evidence": evidence}


async def save_m2_activity_context(db, *, conversation, state, raw, messages):
    """Store sourced M2 activity notes without promoting them to a goal.

    The count of independent trial experiences is deliberately not derived
    from message count: two messages may describe the same activity event.
    """
    if (not isinstance(raw, dict) or not messages or state.get("current_module") != "module_2"
            or state.get("conversation_id") != conversation.id
            or (state.get("memory") or {}).get("sandbox_mode") == "true"):
        return None
    if any(m.conversation_id != conversation.id for m in messages):
        return None
    context = dict((state.get("memory") or {}).get("m2_activity_context") or {})
    changed = False
    intention = raw.get("intention")
    source = source_reference(intention, messages)
    if source is not None and intention.get("value") in {"no_intention", "intention", "action"}:
        context["intention"] = {"value": intention["value"], "message_id": source.id, "quote": intention["quote"]}
        changed = True
    trial = raw.get("trial")
    source = source_reference(trial, messages)
    states = {"none", "trial_active", "trial_completed", "trial_continue", "trial_upgrade_to_core", "trial_retained_secondary"}
    if source is not None and trial.get("state") == "none":
        context["trial"] = {"state": "none", "message_id": source.id, "quote": trial["quote"]}
        changed = True
    elif source is not None and trial.get("state") in states and trial.get("source_role") in {"trial", "secondary"}:
        activity = text(trial.get("activity_content"), 255)
        previous = next((m for m in reversed(messages) if m.position < source.position), None)
        texts = [source.content or ""] + ([previous.content or ""] if previous and previous.role == "assistant" else [])
        if activity and any(activity in value for value in texts):
            context["trial"] = {"state": trial["state"], "activity_content": activity,
                                "source_role": trial["source_role"], "message_id": source.id, "quote": trial["quote"]}
            changed = True
    secondary = raw.get("secondary_activities")
    if isinstance(secondary, list):
        # Retain existing sourced notes on partial extraction. Updating a note
        # uses its activity and source, rather than generating a formal goal.
        notes = {(v["message_id"], v["activity_content"]): v for v in context.get("secondary_activities", [])
                 if isinstance(v, dict) and "message_id" in v and "activity_content" in v}
        for item in secondary[:20]:
            source = source_reference(item, messages)
            activity = text(item.get("activity_content"), 255) if isinstance(item, dict) else None
            if source is None or not activity or activity not in source.content:
                continue
            note = {"activity_content": activity, "message_id": source.id, "quote": item["quote"]}
            for field in ("time", "location", "frequency", "companion", "duration"):
                value = text(item.get(field), 255)
                if value and value in source.content:
                    note[field] = value
            notes[(source.id, activity)] = note
            changed = True
        if changed:
            context["secondary_activities"] = list(notes.values())
    if not changed:
        return None
    runtime = schema.tables["conversation_runtime_states"]
    current = (await db.execute(select(runtime).where(runtime.c.conversation_id == conversation.id).with_for_update())).mappings().one_or_none()
    if current is None or current["current_module"] != "module_2":
        return None
    await db.execute(update(runtime).where(runtime.c.conversation_id == conversation.id).values(
        memory={**(current["memory"] or {}), "m2_activity_context": context},
        row_version=current["row_version"] + 1, updated_at=now()))
    return context


async def save_goal_details(db, goal_id, evidence):
    table = schema.tables["pa_goal_details"]
    # source_activity is a verified working value for the plan/goal title,
    # not a pa_goal_details column. Keep persistence's schema boundary explicit
    # for both inserts and updates (SQLAlchemy rejects unknown update keys).
    fields = {key: evidence[key] for key in (
        "goal_kind", "long_term_direction", "source_message_id",
        "source_conversation_id", "evidence") if key in evidence}
    exists = (await db.execute(select(table.c.goal_id).where(table.c.goal_id == goal_id))).scalar_one_or_none()
    if exists:
        await db.execute(update(table).where(table.c.goal_id == goal_id).values(**fields, updated_at=now()))
    else:
        await db.execute(insert(table), {"goal_id": goal_id, **fields})


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


async def save_review_followup(db, review_id, raw, messages, decision, *, decision_evidence=None):
    table = schema.tables["pa_review_details"]
    existing = (await db.execute(select(table).where(
        table.c.review_id == review_id))).mappings().one_or_none()
    users = [m for m in (messages or []) if m.role == "user"]

    allowed = {1: {"continue"}, 3: {"adjust"}, 4: {"pause", "end"},
               2: {"replace_keep", "replace_pause"}}
    opposite = {
        "end": r"不(?:想|要|再)?结束|先不结束|(?:改为|改成|改选).{0,10}(?:继续|暂停|调整|更换)",
        "pause": r"不(?:想|要|再)?暂停|(?:改为|改成|改选).{0,10}(?:继续|结束|调整|更换)",
        "continue": r"不(?:想|要|再)?继续|(?:改为|改成|改选).{0,10}(?:结束|暂停|调整|更换)",
        "adjust": r"不(?:想|要|再)?调整|(?:改为|改成|改选).{0,10}(?:继续|结束|暂停|更换)",
        "replace_keep": r"不(?:想|要|再)?(?:换|更换)|(?:改为|改成|改选).{0,10}(?:继续|结束|暂停|调整)",
        "replace_pause": r"不(?:想|要|再)?(?:换|更换|暂停)|(?:改为|改成|改选).{0,10}(?:继续|结束|调整)",
    }
    withdrawn = r"撤回.{0,12}(?:决定|选择)|(?:决定|选择).{0,12}撤回|先不决定|还没决定|重新考虑"

    def explicit_continue_conflict(body):
        """Whether a user turn explicitly points away from ``continue``."""
        if not isinstance(body, str):
            return False
        if re.search(
            r"(?:改(?:成|为)|调整|修改|重新(?:讨论|安排|制定|确认)|"
            r"(?:换|更换|替换)(?:成|为)?|改动(?:计划|方案|时长|频率|时间|地点))",
            body,
        ):
            return True
        if re.search(r"暂停|先停|停一阵|暂时不做|先放一放", body):
            return True
        # Negative reports (e.g. "没有完成目标") are not end decisions.
        return bool(re.search(
            r"不再继续|放弃目标|"
            r"(?<!没)(?<!没有)(?<!未)(?<!不想)(?<!不要)(?<!不愿)(?<!不打算)(?:结束|终止)|"
            r"(?<!没)(?<!没有)(?<!未)(?<!不想)(?<!不要)(?<!不愿)(?<!不打算)"
            r"完成(?:这个|本次)?目标",
            body,
        ))

    def explicit_direction(body):
        """Extract a conservative direction marker from a later user turn.

        This is only used to invalidate an old persisted choice.  It does not
        create a new choice; an unmarked or ambiguous turn returns ``None``
        and remains available for the normal extractor/dialogue path.
        """
        if not isinstance(body, str):
            return None
        match = re.search(
            r"(?:我(?:现在|还是|这次)?(?:决定|选择|想|要|打算|愿意)|那就|就)"
            r"(?:按原计划)?(继续|调整|修改|暂停|结束|终止|更换|换个目标|换一个目标)",
            body,
        )
        if match:
            token = match.group(1)
            if token == "继续":
                return "continue"
            if token in {"调整", "修改"}:
                return "adjust"
            if token in {"暂停"}:
                return "pause"
            if token in {"结束", "终止"}:
                return "end"
            return "replace"
        if re.search(r"按原计划继续|照旧(?:执行|做)|维持原计划|保持原计划", body):
            return "continue"
        return None

    def existing_still_valid():
        """Revalidate a persisted choice before carrying it over an omission.

        ``messages`` is the authenticated current-cycle transcript supplied by
        the M4 extractor.  The old row is reusable only when its source is
        still present as the same user message and no later user turn retracts
        or changes that action.  A missing/partial extractor result therefore
        cannot manufacture a decision, while an explicit later change clears
        the row below.
        """
        if not existing or not users:
            return False
        action = existing.get("action")
        if action not in set().union(*allowed.values()):
            return False
        source = next((m for m in users if m.id == existing.get("source_message_id")), None)
        quote = existing.get("source_quote")
        if not source or not isinstance(quote, str) or not quote or quote not in source.content:
            return False
        if decision is not None and action not in allowed.get(decision, set()):
            return False
        def conflicts(body):
            if re.search(withdrawn + "|" + opposite[action], body):
                return True
            if action == "continue" and explicit_continue_conflict(body):
                return True
            direction = explicit_direction(body)
            if direction is None:
                return False
            if action.startswith("replace"):
                return direction != "replace"
            return direction != action

        return not any(conflicts(m.content) for m in users if m.position > source.position)

    def decision_evidence_matches_existing():
        """Check an optional M4 evidence pointer before carrying a row over.

        A ``None`` follow-up means that extraction omitted the follow-up
        object; it does not mean that an explicitly supplied evidence pointer
        can be ignored.  The durable row is the authority for the decision,
        so a pointer supplied with the omission must still identify that same
        user message and literal quote.  A different (or malformed) pointer
        is fail-closed: it may represent a newly selected direction or stale
        evidence and must not silently preserve the old choice.
        """
        if decision_evidence is None:
            return True
        if not isinstance(decision_evidence, dict):
            return False
        source_id = decision_evidence.get("message_id")
        quote = decision_evidence.get("quote")
        if source_id is None or not isinstance(quote, str) or not quote:
            return False
        source = next((m for m in users if m.id == source_id), None)
        if not source or source.role != "user" or quote not in source.content:
            return False
        # The two model fields may choose different literal spans from the
        # same user turn (for example, a short direction phrase versus the
        # surrounding sentence).  Bind them to the same authenticated source
        # message and validate both quotes literally; requiring identical
        # spans would reject a valid structured extraction.
        persisted_quote = existing.get("source_quote")
        return (source.id == existing.get("source_message_id")
                and isinstance(persisted_quote, str) and bool(persisted_quote)
                and persisted_quote in source.content)

    # ``review_followup`` is optional in a later snapshot: for example, a
    # closing summary may be extracted while the already verified decision is
    # omitted.  Preserve only that verified row; malformed or contradictory
    # evidence takes the fail-closed path and removes it.
    if (raw is None and existing_still_valid()
            and decision_evidence_matches_existing()):
        # ``decision_evidence`` may be carried forward by the durable M4
        # contract even when this turn omitted ``review_followup``.  The
        # persisted user decision remains valid and must not be deleted just
        # because the extractor returned a partial snapshot.
        return

    await db.execute(delete(table).where(table.c.review_id == review_id))
    if not isinstance(raw, dict) or not users:
        # If a fresh extractor omitted the optional follow-up object but the
        # durable M4 contract contains a current explicit decision, materialize
        # that verified action instead of leaving readiness without a source.
        # This path is only for a review with no existing row; an existing row
        # still follows the fail-closed omission/change rules above.
        if (existing is None and isinstance(decision_evidence, dict)
                and decision in allowed):
            source = next((m for m in users
                           if m.id == decision_evidence.get("message_id")), None)
            decision_quote = decision_evidence.get("quote")
            action = explicit_direction(decision_quote)
            if (source and source.role == "user"
                    and isinstance(decision_quote, str)
                    and decision_quote
                    and decision_quote in source.content
                    and action in allowed.get(decision, set())):
                await db.execute(insert(table), {
                    "review_id": review_id, "action": action,
                    "source_message_id": source.id,
                    "source_quote": decision_quote,
                })
        return
    # The M4 contract has already resolved the current cycle's actual decision
    # source. A later acknowledgement or request for a closing summary does
    # not erase that choice. Both extracted views must refer to this same row.
    if not isinstance(decision_evidence, dict):
        return
    source = next((m for m in users if m.id == decision_evidence.get("message_id")), None)
    decision_quote = decision_evidence.get("quote")
    if not source or not isinstance(decision_quote, str) or not decision_quote or decision_quote not in source.content:
        return
    action, quote = raw.get("action"), text(raw.get("source_quote"), 2000)
    if action not in allowed.get(decision, set()) or not quote or quote not in source.content:
        if (existing is None and decision in allowed
                and isinstance(decision_quote, str) and decision_quote):
            inferred = explicit_direction(decision_quote)
            if inferred in allowed.get(decision, set()):
                await db.execute(insert(table), {
                    "review_id": review_id, "action": inferred,
                    "source_message_id": source.id,
                    "source_quote": decision_quote,
                })
        return

    # The extractor may classify a user turn as ``continue`` merely because
    # the requested value happens to equal the current plan.  A source turn
    # that explicitly changes, reopens, pauses, or ends the plan is a new
    # direction even when its resulting value is identical.  Keep this guard
    # deliberately lexical and conservative: it is only a backstop against
    # an action/source contradiction, while the user message remains the
    # authority.  In particular, an assistant's "按原计划继续，可以吗？"
    # cannot become evidence because ``source`` is restricted to user rows.
    # A user can mention preserving the goal while changing the plan (the
    # production failure was exactly this shape).  The change marker wins.
    if action == "continue" and explicit_continue_conflict(quote):
        return
    # Explicit later withdrawal/change cannot revive an older decision even
    # if an extractor mistakenly selects its old quote. Ambiguous meaning is
    # still the extractor's responsibility; these are conservative backstops.
    if any(re.search(withdrawn + "|" + opposite[action], m.content)
           for m in users if m.position > source.position):
        return
    if action in {"pause", "replace_pause"} and not re.search(r"暂停|先停|停一阵|暂时不做|先放一放", quote):
        return
    await db.execute(insert(table), {"review_id": review_id, "action": action,
        "source_message_id": source.id, "source_quote": quote})


async def public_activities(db, user_id, *, conversation_id=None):
    table = schema.tables["pa_activity_events"]
    query = select(table).where(table.c.user_id == user_id, table.c.status == "active")
    query = query.where(table.c.source_conversation_id == conversation_id) if conversation_id else query.where(table.c.goal_id.is_(None))
    rows = (await db.execute(query.order_by(table.c.created_at.desc(), table.c.id).limit(30))).mappings().all()
    return [{k: row[k] for k in ("id", "activity_content", "event_kind", "occurred_at_text", "effect", "goal_id")} for row in rows]
