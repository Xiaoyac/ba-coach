"""Evidence-scoped goal classification and activity capture, not LLM authority."""
import re
from sqlalchemy import select, insert, update, delete
from .clinical_fields import Spec
from .database_v2_schema import metadata as schema
from .models import Conversation, ConversationMessage
from .v2_repository import new_id, now

GOAL_SPEC = Spec("goal_proposal", "json", """仅在用户已明确选择要尝试的活动时提取对象，否则 null。
对象字段：goal_kind(primary主要目标/secondary次要目标)，long_term_direction(用户希望长期改善的方向，次要可null)，
selection_quote(本次 M2 对话中用户明确选择活动的逐字原话，可来自较早一轮；不能使用助手单方建议)，activity_quote(具体活动的原文短语，必须出现在用户原话或紧邻该用户回复前的助手建议中)。target_activity_content可以是对用户原话的保守规范化或补充细节，但也必须能在用户原话或紧邻助手建议中逐字找到，不能凭空改写，
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


def _current_activity_choice(body, phrase, previous, selection):
    """Conservative choice evidence, not general intent/semantic classification.

    Inspect the whole turn, not just an extractor-selected substring. Scope a
    negative to its clause so rejecting running does not reject choosing swimming.
    Ambiguous cases stay in dialogue; no goal or plan is written speculatively.
    """
    if re.match(r"不|没|别", phrase):
        return False
    # Quoting an activity name (我选“游泳”) is fine; quoting a full statement
    # (朋友说“我选游泳”) is not the user's own decision.
    scoped = re.sub(r'[“‘"]([^”’"]*)[”’"]',
        lambda m: "[引用]" if re.search(r"我.*(?:想|选|决定|愿意)|你可以", m.group(1)) else m.group(), body)
    clauses = [s.strip() for s in re.split(r"[，,。；;！!\n]|(?:但是|不过|但)", scoped) if s.strip()]
    uncertainty = r"可能|也许|考虑|说不定|不确定|没想好|还没决定|尚未决定"
    negation = r"不想|不打算|不要|先不|没选|不选|先别|选择不|选不|暂时不|不去|不做|不再|(?:决定|打算|想|愿意)不"
    non_choice = r"如果|假如|假设|比如|例如|举例|只是.*(?:例子|引用|转述)|这句话|[？?]|(?:以前|之前|过去).*(?:说过|想|选)|(?:他说|她说|朋友说|助手说|你说|别人说)"
    # A later retraction invalidates an earlier apparent selection, even when
    # the extractor omits that retraction from selection_quote.
    retraction = r"算了|(?:^|，)(?:我)?(?:还没决定|没想好)(?!.*(?:时间|几点|多久|地点|时长|频率))|(?:^|，)(?:我)?先(?:别|不要|不).{0,6}(?:创建|建|保存|设定|做)|只是.*(?:例子|引用|转述)"
    # Natural emphasis words ("自己", "亲自", "本人") do not change the
    # choice semantics. Keep them inside the explicit-choice grammar so a
    # quoted, hypothetical, or subsequently retracted statement is still
    # rejected by the guards above/below. In particular, both
    # "我自己选择散步" and "这次自己选择散步" are first-person choices.
    explicit = (
        r"^(?:(?:好|好的|那|那么|就|这次|现在|我现在|我这次|还是)[\s、]*)*"
        r"(?:我(?:还是)?(?:自己|亲自|本人)?"
        r"(?:选择|选|决定|打算|愿意|想(?:要|试试|尝试|继续)?)|"
        r"(?:自己|亲自|本人)?(?:选择|选|决定|打算|想试试|试试|那就|就))"
    )
    for index, clause in enumerate(clauses):
        if phrase not in clause or re.search(uncertainty + "|" + negation + "|" + non_choice, clause):
            continue
        if index and re.search(r"(?:说|说过|举例)[：:]?$", clauses[index - 1]):
            continue
        tail = "，".join(clauses[index + 1:])
        if re.search(retraction, tail):
            continue
        if re.search(r"(?:改成|换成|改为|换为).{0,20}", tail) and not re.search(
                r"时间|地点|时长|频率|每天|每周|每月|早上|上午|中午|下午|晚上|分钟|小时|日期|星期", tail):
            continue
        if any(phrase in s and re.search(negation + "|" + uncertainty, s) for s in clauses[index + 1:]):
            continue
        # Do not let an intent toward a different activity justify this one.
        choice = re.match(explicit, clause)
        if choice and re.search(r"还是|或者|或|都行|均可", clause[choice.end():]):
            continue
        if (choice and phrase in clause[choice.end():] and phrase in selection
                and (selection in clause or clause in selection)):
            return True

    # Natural Chinese often places the explicit choice marker in one clause
    # and the activity after a comma ("我想约朋友，周日去玩鬼抓人").  Keep the
    # same fail-closed exclusions above, but allow that single-user sentence to
    # bind the later activity when the marker clearly precedes it.
    marker = re.search(r"(?:我(?:想|打算|决定|选择|愿意)|安排我|那就|我觉得可以)", body)
    if (marker and phrase in body and marker.start() < body.find(phrase)
            and selection in body and not re.search(
                uncertainty + "|" + negation + "|" + non_choice + r"|还是|或者|或|、|/", body)):
        return True

    if not previous or previous.role != "assistant":
        return False
    prompt = previous.content.strip()
    # Only the immediate final question can ground an elliptical response.
    question = re.split(r"[。！!？?\n]", prompt.rstrip("？? "))[-1]
    asks_choice = bool(re.search(
        r"(?:愿意|想|要|选择|选).*(?:活动|目标|试试|尝试|吗)|"
        r"(?:选择|选).*(?:哪种|哪个|哪件|哪项|什么)|哪(?:种|个|件|项)",
        question))
    if not asks_choice or re.search(r"理解|明白|知道|举例|比如|例如", question):
        return False
    clean = re.sub(r"[\s，,。！!、‘’“”\"~～]", "", body)
    if clean == phrase and phrase in body:
        return True
    # Agreement to multiple candidates or an educational example is not a
    # selection of whichever candidate the extractor happened to choose.
    # A short answer that exactly repeats one item from an explicitly listed
    # choice set is an unambiguous selection (for example the user says
    # “散步” after “散步、拉伸、收拾屋子，哪件最不费劲？”). The old
    # alternatives guard rejected it merely because the assistant listed the
    # other options in the same question.
    if clean == phrase and phrase in question and re.search(r"哪(?:种|个|件|项)", question):
        return True
    if phrase not in question or re.search(r"还是|或者|或|、|/|举例|比如|例如", prompt):
        return False
    return bool(re.fullmatch(r"(?:好|好的|嗯|可以|行|愿意|我愿意|同意|我同意|就按这个试试|就按这个来|就这样|就选这个)+", clean))


def _selection_turn(raw_selection, phrase, messages):
    """Find the user's explicit activity choice in the persisted transcript.

    Extraction runs over the whole M2 transcript.  A later plan/detail turn or
    the short confirmation that follows a card therefore need not repeat the
    original choice.  The previous implementation required ``selection_quote``
    to occur in the last user message, so a perfectly valid choice became
    uncreatable as soon as extraction completed one turn late.  Keep the
    source bound to a user row and require that no later user turn explicitly
    retracts or replaces that choice.
    """
    users = [m for m in messages if m.role == "user"]
    candidates = [m for m in reversed(users)
                  if isinstance(raw_selection, str) and raw_selection in (m.content or "")]
    for candidate in candidates:
        previous = next((m for m in reversed(messages) if m.position < candidate.position), None)
        if not _current_activity_choice(candidate.content, phrase, previous, raw_selection):
            continue
        later = [m for m in users if m.position > candidate.position]
        # A later correction/replacement is a material change.  It must be
        # handled as a fresh proposal rather than reviving the old activity.
        def replaces_choice(message):
            body = message or ""
            if re.search(r"(?:朋友说|他说|她说|助手说|你说|别人说)", body):
                # A reported suggestion is not the user's replacement choice.
                return False
            # Keep an earlier activity choice valid while the user is merely
            # filling in schedule details (for example, changing the time).
            # A replacement of the activity itself is still a hard boundary.
            if phrase and phrase in body and re.search(
                    r"(?:不做|不选|不想|没选|改成|换成|改为|换为|不是.{0,20}而是)", body):
                return True
            if re.search(r"(?:换个目标|换一个目标|重新安排|算了|先不(?:做|建|创建|保存|设定))", body):
                return True
            if re.search(r"(?:再考虑|重新考虑|还没决定|没想好|暂时不)", body):
                schedule_words = r"时间|地点|时长|频率|每天|每周|每月|早上|上午|中午|下午|晚上|分钟|小时|日期|星期"
                return not re.search(schedule_words, body)
            # A later request can repeat the choice without naming the
            # activity again (for example, "我选择按这份安排执行，请创建
            # 目标").  Treat references to the already reviewed plan as an
            # acknowledgement, not as a replacement.  Keep explicit change
            # language above this guard so "改成/换成" remains a boundary.
            plan_reference = r"(?:这份|这个|上述|之前的|原来的|原定的)(?:安排|计划|方案|目标|决定)"
            change_language = r"改成|换成|改为|换为|换个目标|换一个目标|重新安排|不做|不选|不想"
            refers_to_plan = (re.search(r"(?:选择|选|决定|执行|创建|保存).{0,24}" + plan_reference, body)
                              or re.search(r"(?:按|按照|依照|照着)" + plan_reference, body))
            if refers_to_plan and not re.search(change_language, body):
                return False
            if re.search(r"(?:我|这次).{0,8}(?:选择|选|决定|改成|换成|改为|换为)", body):
                schedule_words = r"时间|地点|时长|频率|每天|每周|每月|早上|上午|中午|下午|晚上|分钟|小时|日期|星期"
                return not re.search(schedule_words, body)
            return False
        if any(replaces_choice(m.content) for m in later):
            continue
        return candidate, previous
    return None, None


def proposal_evidence(raw, messages, activity):
    if not isinstance(raw, dict):
        return None
    users = [m for m in messages if m.role == "user"]
    if not users:
        return None
    selection = text(raw.get("selection_quote"), 2000)
    phrase = text(raw.get("activity_quote"), 255)
    kind = raw.get("goal_kind")
    if kind not in {"primary", "secondary"} or not selection:
        return None
    if not phrase or not isinstance(activity, str) or not activity.strip():
        return None
    latest = users[-1]
    selection_turn, previous = _selection_turn(selection, phrase, messages)
    if selection_turn is None:
        return None
    # ``activity_quote`` is the user's own short label (for example ``散步``),
    # while ``activity_content`` may be a more specific wording (for example
    # ``小区平路慢走五分钟``).  Requiring the quote to be a substring of the
    # display text silently rejected legitimate goals.  Both values remain
    # source-bound: the short label must be in the selected turn (or its
    # immediate assistant proposal), and normalized detail may additionally be
    # present in the card immediately preceding a later confirmation.
    source_texts = [selection_turn.content]
    if previous and previous.role == "assistant":
        source_texts.append(previous.content)
    if latest.id != selection_turn.id:
        latest_previous = next((m for m in reversed(messages) if m.position < latest.position), None)
        if latest_previous and latest_previous.role == "assistant":
            source_texts.append(latest_previous.content)
        source_texts.append(latest.content)
    if not any(phrase in source for source in source_texts):
        return None
    activity_matches_phrase = phrase in activity.strip() or activity.strip() in phrase
    if not any(activity.strip() in source for source in source_texts) and not (
            activity_matches_phrase and any(phrase in source for source in source_texts)):
        return None
    # A coach card may normalize a user's short label with time/location
    # detail, but it cannot add a second activity.  Keep the raw extraction
    # available for diagnostics while returning a source-bound activity for
    # persistence.  The caller must use ``source_activity`` as the goal title
    # and plan activity; never persist a card-only expansion.
    normalized = activity.strip()
    user_bound = any(normalized in (m.content or "") for m in users)
    # A short list choice can be expanded into an executable card by the
    # assistant.  Persist that expansion only when the user immediately
    # affirmed the card; otherwise keep the user's literal short label and do
    # not let an assistant suggestion become a goal title.
    latest_previous = next((m for m in reversed(messages)
                            if m.position < latest.position and m.role == "assistant"), None)
    compact_latest = re.sub(r"[\s，。！!,.、~～；;：:]", "", latest.content or "")
    card_ack = bool(latest_previous and normalized in (latest_previous.content or "")
                    and re.fullmatch(
                        r"(?:好|好的|好呀|好啊|好哒|嗯|嗯嗯|可以|可以的|可以呀|没问题|行|对|对的|是的|确认|就这样|就这么定|没错)+",
                        compact_latest))
    source_activity = normalized if user_bound or card_ack else phrase
    direction = text(raw.get("long_term_direction"))
    direction_quote = text(raw.get("direction_quote"), 2000)
    if kind == "primary" and (not direction or not direction_quote or
        not any(direction_quote in m.content for m in users) or direction not in direction_quote):
        return None
    return {"goal_kind": kind, "long_term_direction": direction if kind == "primary" else None,
        "source_message_id": selection_turn.id, "source_conversation_id": selection_turn.conversation_id,
        "source_activity": source_activity,
        "evidence": {"selection_quote": selection, "activity_quote": phrase, "direction_quote": direction_quote}}


def recover_goal_proposal(messages, activity):
    """Recover an omitted extractor proposal only from explicit user choice text.

    The extractor may return a complete PA card while omitting ``goal_proposal``.
    This fallback never treats an assistant card or a bare activity mention as a
    choice: it needs a user sentence with a first-person choice marker and the
    activity phrase bound to that same sentence.
    """
    if not isinstance(activity, str) or not activity.strip():
        return None
    users = [m for m in messages if m.role == "user"]
    for message in reversed(users):
        body = (message.content or "").strip()
        if not body or re.search(r"还没决定|没想好|不确定|可能|也许|先不|算了|不想|不打算", body):
            continue
        # Prefer the longest contiguous activity span still present in the
        # user's sentence; card-only modifiers cannot become evidence.
        candidate = activity.strip()
        if candidate not in body:
            pieces = [candidate[i:j] for i in range(len(candidate))
                      for j in range(i + 2, len(candidate) + 1)
                      if candidate[i:j] in body]
            if not pieces:
                continue
            candidate = max(pieces, key=len)
        if not re.search(r"我(?:想|打算|决定|选(?:择)?|愿意)|安排我|那就|我觉得可以", body):
            continue
        # The marker and activity must occur in one forward clause; this
        # rejects quoted examples and detached activity observations.
        if not re.search(r"(?:我(?:想|打算|决定|选(?:择)?|愿意)|安排我|那就|我觉得可以).{0,120}" + re.escape(candidate), body):
            continue
        direction_quote = next((m.content.strip() for m in users if re.search(
            r"希望.{0,12}(?:改变|改善)|想让.{0,12}(?:改变|改善)", m.content or "")), None)
        if direction_quote:
            return {"goal_kind": "primary", "long_term_direction": direction_quote,
                    "selection_quote": body, "activity_quote": candidate,
                    "direction_quote": direction_quote}
        return {"goal_kind": "secondary", "long_term_direction": None,
                "selection_quote": body, "activity_quote": candidate}

    # A user can select one item from a short list with the item alone.  The
    # phrase is still source-bound to the immediately preceding assistant
    # question; it is not a bare activity mention.  Later assistant cards may
    # expand that choice into the executable plan text passed as ``activity``.
    for message in reversed(users):
        body = (message.content or "").strip()
        if not body or len(body) > 24 or re.search(r"[？?，,。；;]|(?:可能|也许|不确定|还没|先不|算了)", body):
            continue
        previous = next((m for m in reversed(messages) if m.position < message.position), None)
        if not previous or previous.role != "assistant":
            continue
        candidate = body.rstrip("吧呀啊呢")
        if len(candidate) < 2 or re.search(r"第[一二三四五六七八九十123456789]个|^(?:好的?|嗯+|可以|行|对|是的)$", candidate):
            continue
        # A bare reply is a choice only when the immediate coach question
        # actually listed that exact option.  Without this source check,
        # generic replies such as “应该可以” were mistaken for activities
        # merely because they satisfy ``clean == phrase`` below.
        if candidate not in (previous.content or ""):
            continue
        if not _current_activity_choice(body, candidate, previous, body):
            continue
        # Require a later assistant/user source for the executable expansion;
        # an isolated “散步” mention must not create a goal by itself.
        later_sources = [m.content or "" for m in messages if m.position >= message.position]
        if isinstance(activity, str) and activity.strip() and not any(activity.strip() in source for source in later_sources):
            continue
        direction_quote = next((m.content.strip() for m in users if re.search(
            r"希望.{0,12}(?:改变|改善)|想让.{0,12}(?:改变|改善)", m.content or "")), None)
        proposal = {"goal_kind": "primary" if direction_quote else "secondary",
                    "long_term_direction": direction_quote if direction_quote else None,
                    "selection_quote": body, "activity_quote": candidate}
        if direction_quote:
            proposal["direction_quote"] = direction_quote
        return proposal
    return None


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
