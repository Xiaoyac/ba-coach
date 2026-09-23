"""Commit conversational consent, never inferred consent from opening a panel.

M1/M4 reuse source-checked contracts. M2/M3 bind an explicit reply to the
immediately preceding summary AND the unchanged draft shown in that turn.
Complex consent can use a bounded language classifier; ownership, versions
and submission remain program checks, with no synthetic user messages.
"""
import hashlib
import json
import re
from collections.abc import Mapping
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select, update
from .database_v2_schema import metadata as schema
from .models import ConversationMessage
from .program_confirmation import draft, record_hash, validate_confirmation, commit_confirmation
from .v2_repository import V2Conflict, V2NotFound, now

record_hash_fn = record_hash
from .v2_workflow import runtime_for, module_extraction_is_current


_CONFIRMATION_FIELDS = (
    "activity_content", "schedule_text", "scheduled_start_at", "timezone",
    "location", "duration_minutes", "frequency_rule", "companion",
    "potential_barriers", "barrier_coping_plan",
)

_M3_CONFIRMATION_FIELDS = (
    "record_requirement", "negotiated_record_plan", "feedback_mechanism",
)


def _plan_text(value):
    """Return the user-facing recording agreement from its JSON value.

    M3 records were stored as plain text by the first V2 migration and as a
    ``{schema_version, text}`` object afterwards.  Confirmation must treat a
    missing/blank value the same in both representations; a truthy empty JSON
    object is not an executable recording plan.
    """
    if isinstance(value, Mapping):
        value = value.get("text")
    return value.strip() if isinstance(value, str) and value.strip() else None


def confirmation_fields_complete(module, record):
    """Check the minimum executable contract before creating evidence.

    This is deliberately a shape check, not a semantic consent decision.  It
    is shared by marker creation, confirmation-only extraction and the final
    dialogue gate so an empty M3 plan cannot become ``awaiting`` merely
    because the Router supplied all of its step names.
    """
    if not isinstance(record, Mapping):
        return False
    if module == "module_3":
        return bool(_plan_text(record.get("negotiated_record_plan"))
                    and isinstance(record.get("record_requirement"), str)
                    and record["record_requirement"].strip()
                    and isinstance(record.get("feedback_mechanism"), str)
                    and record["feedback_mechanism"].strip())
    if module == "module_2":
        from .plan_contract import missing_plan_fields
        try:
            return not missing_plan_fields(record)
        except (KeyError, TypeError):
            return False
    return True


def confirmation_marker_matches(module, marker, record, *, cycle_id=None,
                                preceding_assistant_id=None,
                                session_id=None):
    """Validate the immutable card marker used by a confirmation-only turn.

    The marker is written when the assistant card is rendered.  Comparing its
    structured snapshot and fingerprint means a later extractor rewrite does
    not have to reproduce the assistant's wording, while still refusing to
    reuse an unverified, empty, stale or unrelated card.
    """
    if not isinstance(marker, Mapping) or marker.get("module") != module:
        return False
    if cycle_id is not None and marker.get("cycle_id") != cycle_id:
        return False
    # New markers are scoped to both the chat and the active cycle.  Keep the
    # optional check backwards compatible with markers written before the
    # session field was introduced; callers that require a versioned marker
    # should pass ``session_id`` and reject a missing value themselves.
    if (session_id is not None and marker.get("session_id") is not None
            and marker.get("session_id") != session_id):
        return False
    if (preceding_assistant_id is not None
            and marker.get("assistant_message_id") != preceding_assistant_id):
        return False
    if marker.get("summary_verified") is not True or marker.get("field_complete") is not True:
        return False
    if not confirmation_fields_complete(module, record):
        return False
    snapshot = marker.get("snapshot")
    return (isinstance(snapshot, Mapping)
            and snapshot == _confirmation_snapshot(module, record)
            and marker.get("fingerprint") == fingerprint(record))


def _confirmation_snapshot(module, record):
    """Capture the contract shown in the assistant's confirmation card.

    Runtime memory is JSON, so dates and other DB values are converted to a
    JSON-safe representation.  This snapshot is an internal evidence anchor,
    not text supplied by the user.
    """
    keys = _CONFIRMATION_FIELDS if module == "module_2" else _M3_CONFIRMATION_FIELDS
    values = {key: record.get(key) for key in keys if key in record}
    return json.loads(json.dumps(values, default=str, ensure_ascii=False))


def fingerprint(record):
    """Fingerprint only what the user actually confirms on the goal card.

    Extraction may harmlessly rephrase ``core_values_impact`` or other
    narrative context when the user says “按这个计划试试”.  Those fields are
    not the card's executable contract and must not invalidate an otherwise
    unchanged confirmation.  Dates, frequency, barriers and the activity stay
    in the fingerprint because changing them is a material plan edit.
    """
    if "activity_content" in record:
        payload = {k: record.get(k) for k in _CONFIRMATION_FIELDS if k in record}
    else:
        # M3 shares this helper; its recording agreement is not an M2 plan.
        ignored = {"created_at", "updated_at", "confirmation_message_id",
                   "confirmation_status", "record_status", "acceptance_feeling"}
        payload = {k: v for k, v in record.items() if k not in ignored}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str,
                                     ensure_ascii=False).encode()).hexdigest()


def affirmative(text):
    # Corrections/questions must never become consent merely because they
    # contain “同意” or “可以”.  After the negative guard, accept ordinary
    # conversational confirmations rather than requiring a fixed password.
    text = str(text or "")
    clean = re.sub(r"[\s，。！!,.、~～；;：:]", "", text)
    if not clean or re.search(r"不同意|不愿意|不可以|没同意|还没决定|先等等|再想想|改成|改为|换成|调整|修改|但是|不过|然而|只是|如果|除非|重新安排|不做了|先不", clean):
        return False
    if re.search(r"他说|她说|朋友说|有人说|引用|[？?]", clean):
        return False
    exact = re.fullmatch(
        r"(?:好|好的|好呀|好啊|好哒|嗯|嗯嗯|可以|可以的|没问题|行|愿意|我愿意|同意|我同意|确认|确认了|"
        r"就这样|就这么定|就按这个来|就按这个计划|就按这个计划试试|按这个计划试试|我愿意按这个计划试试|"
        r"没错|没错就是这样|没错按这个来|"
        r"就按这个方式记录|我同意这样记录|我会这样记录|可以就这样|这个安排挺合适的|我觉得这个安排挺合适的|"
        r"对|对的|是的|这就是我的意思|我确认并同意|我确认并同意这个记录方法)", clean)
    if exact:
        return True
    if re.fullmatch(
        r"(?:对|对的|是的|好|好的|可以|行|确认|同意|我同意|我确认|愿意|没问题)"
        r"(?:这个|这样|按这个|按照这个|就按这个|照这个|这份|该)"
        r"(?:安排|计划|方式|记录|意思|执行|试试|来|定)", clean):
        return True
    # Consume the WHOLE confirmation-only utterance. A positive substring
    # inside “我不太同意这样记录”, a quotation, or a plan amendment is not
    # consent. Unknown clauses remain discussion; never freeze the old draft
    # just because the utterance contains "同意" somewhere.
    acknowledgement = r"(?:好的|好呀|好啊|好哒|好|嗯嗯|嗯|可以的|可以|没问题|行|对的|对|是的|没错|确认了|确认|我确认|我同意|同意|我愿意|愿意|就这样|就这么定|这就是我的意思)"
    agreement = (r"(?:我)?(?:确认并同意|确认|同意|愿意采用|愿意尝试)"
                 r"(?:这个|这份|刚才的|你整理的)(?:完整)?(?:计划|安排|记录方法|记录方式|记录约定)")
    action = (r"(?:我愿意|我会|我就|就|那就)?(?:按|按照|照)"
              r"(?:这个|刚才这个|刚才的|你整理的)(?:计划|方式|安排)?"
              r"(?:试试|来|执行|记录|做|定)")
    evaluation = (r"(?:我觉得|我认为)?(?:这个安排|这个计划|这个记录方法|这个记录方式|你整理的记录方式)"
                  r"(?:挺|很|完全|都|基本)?(?:合适的|合适|准确|正确|没问题)")
    # Natural ability-based confirmations are common in Chinese conversation:
    # “这个记录方式我能做到，就这么安排吧”.  They affirm the displayed
    # contract without using a fixed “同意/确认” keyword.  Keep the complete
    # utterance anchored and retain the negative/correction guard above.
    ability = (r"(?:这个|这份|刚才的|你整理的)?(?:记录(?:方法|方式)?|计划|安排)"
               r"(?:我)?(?:能|可以|能够)(?:做到|执行|坚持|完成|接受)"
               r"(?:就(?:这么|这样)?(?:安排|定|执行|来)?|按这个(?:计划|方式)?(?:来|做|执行)?)?吧?")
    ability_first = (r"(?:我)?(?:能|可以|能够)(?:做到|执行|坚持|完成|接受)"
                     r"(?:这个|这份|刚才的|你整理的)?(?:记录(?:方法|方式)?|计划|安排)"
                     r"(?:就(?:这么|这样)?(?:安排|定|执行|来)?|按这个(?:计划|方式)?(?:来|做|执行)?)?吧?")
    return bool(re.fullmatch(
        rf"(?:{acknowledgement}|{agreement}|{action}|{evaluation}|{ability}|{ability_first}|我同意这样记录|我会这样记录)+", clean))


def summary_present(module, text, record):
    if re.search(r"(?:目标面板|网页|按钮).{0,24}(?:确认|保存|提交)", text):
        return False
    if not re.search(r"确认|愿意|可以吗|合适吗|符合|对吗|正确吗|怎么样|可行吗|觉得如何", text):
        return False
    compact = lambda x: re.sub(r"[\s*#`，,。；;]", "", str(x or "")).replace("：", ":")
    body = compact(text)
    if module == "module_2":
        if not confirmation_fields_complete("module_2", record):
            return False
        # The card is a formatted rendering, not a verbatim copy of the
        # user's schedule sentence. Require every executable field, while
        # allowing the UI/agent to split “下周一中午十二点半” into date and
        # time lines. This prevents a hidden plan from being confirmed but
        # accepts ordinary formatting and punctuation changes.
        if not all(compact(record[k]) in body for k in ("activity_content", "location")):
            return False
        if str(record["duration_minutes"]) not in body:
            return False
        frequency = record.get("frequency_rule") or {}
        if not compact(frequency.get("text")) or compact(frequency["text"]) not in body:
            return False
        from .plan_contract import _barrier_normalized
        if any(compact(x) not in body
               and compact(_barrier_normalized(x)) not in body
               for x in record["potential_barriers"]):
            return False
        if any(compact(x["plan"]) not in body for x in record["barrier_coping_plan"]):
            return False
        return _schedule_visible(record, body)
    if not confirmation_fields_complete("module_3", record):
        return False
    plan = _plan_text(record.get("negotiated_record_plan"))
    return bool(plan and compact(plan) in body)


def render_confirmation_summary(module, record, *, question=True):
    """Render the exact structured contract used as a confirmation anchor.

    The model may still add empathy around this text, but the executable
    fields themselves come from the persisted draft.  Keeping this renderer
    deterministic gives the user one stable version to confirm and lets
    ``summary_present`` verify the same content without brittle whole-sentence
    matching.  An empty/incomplete record returns ``None`` so callers cannot
    accidentally display a confirmation card for a hidden draft.
    """
    if not confirmation_fields_complete(module, record):
        return None
    lines = []
    if module == "module_3":
        lines.extend([
            f"记录内容：{record['record_requirement'].strip()}",
            f"记录方式：{_plan_text(record['negotiated_record_plan'])}",
            f"遇到困难时：{record['feedback_mechanism'].strip()}",
        ])
    elif module == "module_2":
        frequency = record.get("frequency_rule") or {}
        freq = frequency.get("text") if isinstance(frequency, Mapping) else frequency
        barriers = record.get("potential_barriers") or []
        coping = record.get("barrier_coping_plan") or []
        lines.extend([
            f"活动：{record['activity_content']}",
            f"时间：{record.get('schedule_text') or _format_schedule(record.get('scheduled_start_at'))}",
            f"地点：{record['location']}",
            f"时长：{record['duration_minutes']}分钟",
            f"频率：{freq}",
            f"潜在障碍：{'、'.join(str(item) for item in barriers) or '暂未发现'}",
            "应对方案：" + "；".join(
                f"{item.get('barrier', '')}：{item.get('plan', '')}" for item in coping
            ) if coping else "应对方案：暂未约定",
        ])
    else:
        return None
    if question:
        lines.append("这份安排可以吗？")
    return "\n".join(lines)


def _format_schedule(value):
    """Format a normalized datetime for the deterministic M2 card."""
    if not value:
        return ""
    raw = str(value).replace("T", " ").replace("Z", "")
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})[ ](\d{1,2}):(\d{2})", raw)
    if not match:
        return raw
    year, month, day, hour, minute = (int(item) for item in match.groups())
    return f"{year}年{month}月{day}日{hour}点{minute:02d}分"


def _schedule_visible(record, body):
    """Check date/time tokens without requiring the original sentence.

    ``schedule_text`` is intentionally not compared as one substring because
    cards render it as separate date, time and frequency rows. The stored
    ``scheduled_start_at`` is the normalized source of truth.
    """
    value = record.get("scheduled_start_at")
    if not value:
        schedule = re.sub(r"[\s*#`，,。；;]", "", str(record.get("schedule_text") or "")).replace("：", ":")
        return bool(schedule and schedule in body)
    raw = str(value).replace("Z", "")
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{2})", raw)
    if not match:
        return False
    year, month, day, hour, minute = (int(x) for x in match.groups())
    # A year is optional in Chinese cards (“9月21日” is normal).  If a year
    # is printed, it must agree; this catches silent 2026→2025 drift.
    explicit_years = re.findall(r"(\d{4})年", body)
    iso_years = re.findall(r"(\d{4})-\d{1,2}-\d{1,2}", body)
    year_ok = all(y == str(year) for y in explicit_years + iso_years)
    date_ok = year_ok and (f"{month}月{day}日" in body or f"{year}年{month}月{day}日" in body
               or f"{year}-{month:02d}-{day:02d}" in body)
    if not date_ok:
        return False
    time_ok = bool(re.search(rf"(?<!\d)0?{hour}:{minute:02d}(?!\d)", body)
                   or re.search(rf"(?<!\d)0?{hour}点0?{minute}分", body)
                   or (minute == 0 and re.search(rf"(?<!\d)0?{hour}点(?:整|钟)?(?![\d半])", body))
                   or (minute == 30 and re.search(rf"(?<!\d)0?{hour}点半", body)))
    return time_ok


async def advance_from_dialogue(db, *, session_id, user_id, assistant_message_id):
    """Called under the turn/profile/runtime locks, in the routing transaction."""
    conversation, state = await runtime_for(db, session_id)
    if (not conversation or conversation.subject_id != user_id or not state
            or state["flow_status"] in {"paused", "completed"}
            or (state["memory"] or {}).get("sandbox_mode") == "true"):
        return None
    rows = list(reversed((await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation.id).order_by(
            ConversationMessage.position.desc()).limit(3))).scalars().all()))
    if len(rows) < 2 or rows[-1].id != assistant_message_id or rows[-1].role != "assistant" or rows[-2].role != "user":
        return None
    pending = await draft(db, state, user_id)
    if not pending:
        return None
    module, user = state["current_module"], rows[-2]
    memory = state["memory"] or {}
    accepted = False
    if module == "module_1":
        from .m1_contract import contract_for, consent_is_current
        contract = contract_for(pending)
        consent = contract.get("evidence", {}).get("consent", {})
        quote = consent.get("quote")
        # A fresh full snapshot can still cite valid consent from an earlier
        # turn. Do not make the user repeat it merely because extraction failed
        # on the original turn. Bind to the original owned user row, never the
        # latest unrelated user text or another conversation's evidence.
        full_rows = list((await db.execute(select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id).order_by(
                ConversationMessage.position, ConversationMessage.id))).scalars())
        turns = [(row.role, row.content) for row in full_rows if row.content]
        source_rows = [row for row in full_rows if row.content]
        index = consent.get("turn")
        snapshot_matches = contract.get("transcript_hash") == hashlib.sha256(
            json.dumps(turns, ensure_ascii=False).encode()).hexdigest()
        if (snapshot_matches and type(index) is int and 0 <= index < len(source_rows)
                and consent.get("role") == "user" and isinstance(quote, str) and quote
                and quote in source_rows[index].content and consent_is_current(turns, index)):
            user, accepted = source_rows[index], True
        elif not contract.get("transcript_hash"):
            # Compatibility for legacy latest-turn evidence only. Historical
            # reuse requires the hash/index path above and cannot fall back here.
            latest_index = next((i for i, row in enumerate(source_rows) if row.id == user.id), -1)
            accepted = (isinstance(quote, str) and bool(quote) and quote in user.content
                        and consent_is_current(turns, latest_index))
    elif module == "module_4":
        from .m4_contract import contract_for, repeated_decision_matches
        evidence = contract_for(pending).get("evidence", {}).get("decision_quote", {})
        accepted = evidence.get("message_id") == user.id
        if not accepted and evidence.get("position", -1) < user.position:
            # The contract keeps the first still-effective decision as the
            # audit anchor. A later explicit repetition is still a valid
            # confirmation after a summary/recovery turn; requiring the
            # original quote to be the immediately preceding user row makes
            # normal retries stall in M4 with a draft review forever.
            details = schema.tables["pa_review_details"]
            action = (await db.execute(select(details.c.action).where(
                details.c.review_id == pending["id"]))).scalar_one_or_none()
            accepted = repeated_decision_matches(user.content, action)
    else:
        previous = memory.get("dialogue_draft", {})
        # Bind this turn to the exact card displayed previously.  The normal
        # extraction path preserves the card fields on a confirmation-only
        # user turn (see ``persist_record``).  Do not silently restore a
        # snapshot here: if a material edit reached this point, failing closed
        # is safer than confirming a plan the user did not see.
        preceding_assistant_id = rows[0].id if len(rows) == 3 and rows[0].role == "assistant" else None
        marker_ok = confirmation_marker_matches(
            module, previous, pending, cycle_id=state["active_cycle_id"],
            session_id=session_id,
            preceding_assistant_id=preceding_assistant_id)
        # The first goal can be created one assistant turn after the user
        # confirms a complete M2 card: extraction has no cycle to persist
        # until that next reply.  In that narrow legacy shape there is no
        # durable marker yet, so bind the immediately preceding, fully
        # rendered card and the user's adjacent affirmative together.  Later
        # turns always use the structured marker path above.
        legacy_lagged_card = (
            not previous and len(rows) == 3 and rows[0].role == "assistant"
            and affirmative(user.content)
            and confirmation_fields_complete(module, pending)
            and summary_present(module, rows[0].content, pending)
        )
        # Compatibility with markers written before the structured snapshot
        # was introduced.  This path still requires a complete plan and a
        # positively verified card; an empty/unverified card cannot qualify.
        if not marker_ok and previous.get("summary_verified") is None and len(rows) == 3 and rows[0].role == "assistant":
            # Compatibility with markers written before summary_verified was
            # introduced. New markers use the immutable result below.
            marker_ok = (confirmation_fields_complete(module, pending)
                         and summary_present(module, rows[0].content, pending))
        accepted = legacy_lagged_card or (
            len(rows) == 3 and rows[0].role == "assistant" and affirmative(user.content)
            and previous.get("module") == module and previous.get("cycle_id") == state["active_cycle_id"]
            and previous.get("assistant_message_id") == rows[0].id
            and previous.get("fingerprint") == fingerprint(pending)
            and marker_ok)
    if accepted and state["last_transition_reason"] == "awaiting_record_confirmation":
        payload = SimpleNamespace(record_id=pending["id"], record_hash=record_hash(pending), row_version=state["row_version"])
        try:
            async with db.begin_nested():
                pending, action = await validate_confirmation(db, conversation=conversation, state=state,
                    user_id=user_id, session_id=session_id, payload=payload)
                return await commit_confirmation(db, conversation=conversation, state=state, user_id=user_id,
                    pending=pending, message=user, review_action=action, snapshot_hash=payload.record_hash,
                    boundary_message_id=assistant_message_id)
        except (HTTPException, V2Conflict, V2NotFound):
            # Keep discussing; never turn a failed check into a committed state.
            pass
    if module in {"module_2", "module_3"} and await module_extraction_is_current(
            db, conversation_id=conversation.id, state=state, module=module, assistant_message_id=assistant_message_id):
        marker = {"module": module, "session_id": session_id, "cycle_id": state["active_cycle_id"],
            "assistant_message_id": assistant_message_id, "fingerprint": fingerprint(pending),
            "snapshot": _confirmation_snapshot(module, pending),
            # Verify the card at the moment it is displayed, before a later
            # extraction can rephrase the draft. Confirmation then checks this
            # boolean plus the stable executable fingerprint.
            "field_complete": confirmation_fields_complete(module, pending),
            "summary_verified": (confirmation_fields_complete(module, pending)
                                  and summary_present(module, rows[-1].content, pending))}
        rt = schema.tables["conversation_runtime_states"]
        await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
            memory={**memory, "dialogue_draft": marker}))
    return None


async def precommit_user_confirmation(db, *, session_id, user_id,
                                      user_message_id, confirmation_provider=None):
    """Commit an M2/M3 confirmation before the next reply is generated.

    Ordinary routing remains asynchronous.  A user confirmation is different:
    the following reply may legitimately say that the plan or recording
    agreement is confirmed, so the state change must settle first.  The user
    message is required to be immediately after the assistant card anchored in
    ``dialogue_draft``; no free-form transcript search is used here.

    ``None`` means this turn is not a confirmation candidate (including a
    correction, stale card, or incomplete draft).  A committed result returns
    the same next-module tuple as ``commit_confirmation``.
    """
    conversation, state = await runtime_for(db, session_id)
    if (not conversation or conversation.subject_id != user_id or not state
            or state["current_module"] not in {"module_2", "module_3"}
            or state["flow_status"] in {"paused", "completed"}
            or state["last_transition_reason"] != "awaiting_record_confirmation"):
        return None
    user = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.id == user_message_id,
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.role == "user"))).scalar_one_or_none()
    if user is None:
        return None
    previous = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.role == "assistant",
        ConversationMessage.position < user.position).order_by(
            ConversationMessage.position.desc(), ConversationMessage.id.desc()).limit(1))).scalar_one_or_none()
    if previous is None:
        return None
    pending = await draft(db, state, user_id)
    marker = (state.get("memory") or {}).get("dialogue_draft", {})
    if not confirmation_marker_matches(
        state["current_module"], marker, pending,
        cycle_id=state["active_cycle_id"],
        session_id=session_id,
        preceding_assistant_id=previous.id,
    ):
        return None
    semantic = False
    if not affirmative(user.content):
        from .confirmation_intent import semantic_confirmation
        semantic = await semantic_confirmation(confirmation_provider,
            user_text=user.content, assistant_text=previous.content,
            module=state['current_module'])
        if not semantic:
            return None
    payload = SimpleNamespace(
        record_id=pending["id"], record_hash=record_hash(pending),
        row_version=state["row_version"],
    )
    try:
        async with db.begin_nested():
            # Language interpretation may await a provider. Re-lock and read
            # the actual state after that wait; another chat must not change
            # the cycle/version while this old proposal is being committed.
            profiles = schema.tables['user_profile']
            await db.execute(select(profiles.c.uuid).where(
                profiles.c.uuid == user_id).with_for_update())
            rt = schema.tables['conversation_runtime_states']
            state = (await db.execute(select(rt).where(
                rt.c.conversation_id == conversation.id).with_for_update())).mappings().one()
            pending, action = await validate_confirmation(
                db, conversation=conversation, state=state, user_id=user_id,
                session_id=session_id, payload=payload,
                extraction_assistant_message_id=previous.id,
                confirmation_user_message_id=user.id,
            )
            return await commit_confirmation(
                db, conversation=conversation, state=state, user_id=user_id,
                pending=pending, message=user, review_action=action,
                snapshot_hash=payload.record_hash,
                source="dialogue_precommit_semantic" if semantic else "dialogue_precommit",
                boundary_message_id=user.id,
            )
    except (HTTPException, V2Conflict, V2NotFound):
        return None


async def anchor_rendered_summary(
    db, *, session_id, user_id, assistant_message_id,
    record_id=None, record_hash=None,
):
    """Anchor a deterministic M2/M3 card after it has been persisted.

    This is intentionally an evidence operation, not a confirmation.  The
    caller has already generated and stored the visible reply; we only mark it
    as confirmable when the exact current draft still matches the supplied
    identity/hash when one is provided, and the assistant body is byte-for-byte
    the deterministic renderer. When a model reproduced that renderer without
    telemetry, the current draft is selected only by that same exact-body
    check. That lets the next user turn confirm the card without asking an
    extractor to reproduce or paraphrase its fields.
    """
    conversation, state = await runtime_for(db, session_id)
    if not conversation or conversation.subject_id != user_id or not state:
        return False
    # Serialize with the same profile→runtime order as record_steps and
    # precommit.  Re-read after taking the locks so a stale background task
    # cannot overwrite a newer card or transition from another chat.
    profiles = schema.tables["user_profile"]
    await db.execute(select(profiles.c.uuid).where(
        profiles.c.uuid == user_id).with_for_update())
    rt = schema.tables["conversation_runtime_states"]
    state = (await db.execute(select(rt).where(
        rt.c.conversation_id == conversation.id).with_for_update())).mappings().one_or_none()
    if (not state or state["current_module"] not in {"module_2", "module_3"}
            or state["flow_status"] in {"paused", "completed"}):
        return False
    latest = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation.id).order_by(
            ConversationMessage.position.desc(), ConversationMessage.id.desc()).limit(1))).scalar_one_or_none()
    if latest is None or latest.id != assistant_message_id:
        return False
    # A deterministic card can replace fragile wording checks, but it cannot
    # skip the M2 discussion that makes the activity an intentional choice.
    # Keep the first three M2 progress gates (PA understanding, intention and
    # activity selection) authoritative; only the card confirmation itself is
    # allowed to open the final awaiting boundary.
    if state["current_module"] == "module_2" and state.get("last_transition_reason") != "awaiting_record_confirmation":
        progress_table = schema.tables["pa_cycle_progress"]
        progress = (await db.execute(select(progress_table).where(
            progress_table.c.cycle_id == state["active_cycle_id"]))).mappings().one_or_none()
        required = {"pa_concept_understood", "values_or_intention_explored", "activity_selected"}
        if not progress or not required.issubset(set(progress["module_2_steps"] or [])):
            return False
    assistant = (await db.execute(select(ConversationMessage).where(
        ConversationMessage.id == assistant_message_id,
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.role == "assistant"))).scalar_one_or_none()
    if assistant is None:
        return False
    pending = await draft(db, state, user_id)
    if not pending:
        return False
    # A workflow-generated card carries its record id/hash in telemetry.  A
    # model may also reproduce the deterministic card verbatim without that
    # telemetry marker; in that case the durable draft itself is the candidate
    # and the exact rendered-body check below remains the deciding evidence.
    if record_id is not None and pending["id"] != record_id:
        return False
    if record_hash is not None and record_hash_fn(pending) != record_hash:
        return False
    rendered = render_confirmation_summary(state["current_module"], pending)
    if not rendered or assistant.content.strip() != rendered.strip():
        return False
    marker = {
        "module": state["current_module"], "session_id": session_id,
        "cycle_id": state["active_cycle_id"],
        "assistant_message_id": assistant_message_id,
        "fingerprint": fingerprint(pending),
        "snapshot": _confirmation_snapshot(state["current_module"], pending),
        "field_complete": confirmation_fields_complete(state["current_module"], pending),
        "summary_verified": True,
    }
    memory = dict(state.get("memory") or {})
    freshness = dict(memory.get("module_extraction_freshness") or {})
    freshness[state["current_module"]] = {
        "assistant_message_id": assistant_message_id,
        "cycle_id": state["active_cycle_id"],
    }
    memory.update({"dialogue_draft": marker, "module_extraction_freshness": freshness})
    await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
        memory=memory, last_transition_reason="awaiting_record_confirmation",
        row_version=state["row_version"] + 1, updated_at=now()))
    return True
