"""Commit conversational consent, never inferred consent from opening a panel.

M1/M4 reuse source-checked contracts. M2/M3 bind an explicit reply to the
immediately preceding summary AND the unchanged draft shown in that turn.
No new LLM call, schema, or synthetic user message is required.
"""
import hashlib
import json
import re
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select, update
from .database_v2_schema import metadata as schema
from .models import ConversationMessage
from .program_confirmation import draft, record_hash, validate_confirmation, commit_confirmation
from .v2_repository import V2Conflict, V2NotFound
from .v2_workflow import runtime_for, module_extraction_is_current


def fingerprint(record):
    ignored = {"created_at", "updated_at", "confirmation_message_id", "confirmation_status", "record_status", "acceptance_feeling"}
    return hashlib.sha256(json.dumps({k: v for k, v in record.items() if k not in ignored},
        sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def affirmative(text):
    # Deliberately conservative. Corrections/questions require a fresh summary,
    # not a substring match on “同意” inside “不同意” or “同意，但是…”.
    clean = re.sub(r"[\s，。！!,.、~～]", "", text)
    return bool(re.fullmatch(
        r"(?:好|好的|好呀|好啊|好哒|嗯|嗯嗯|可以|可以的|没问题|行|愿意|我愿意|同意|我同意|确认|确认了|"
        r"就这样|就这么定|就按这个来|就按这个计划|就按这个计划试试|按这个计划试试|我愿意按这个计划试试|"
        r"就按这个方式记录|我同意这样记录|我会这样记录|可以就这样|这个安排挺合适的|我觉得这个安排挺合适的)+", clean))


def summary_present(module, text, record):
    if re.search(r"(?:目标面板|网页|按钮).{0,24}(?:确认|保存|提交)", text):
        return False
    if not re.search(r"确认|愿意|可以吗|合适吗|怎么样|可行吗|觉得如何", text):
        return False
    compact = lambda x: re.sub(r"[\s*#`，,。；;：:]", "", str(x or ""))
    body = compact(text)
    if module == "module_2":
        from .plan_contract import missing_plan_fields
        if missing_plan_fields(record):
            return False
        # The actual core plan must be visible; an unrelated “可以吗” cannot
        # authorise a hidden draft. If wording differs, ask again in chat.
        return (all(compact(record[k]) in body for k in ("activity_content", "schedule_text", "location"))
            and str(record["duration_minutes"]) in body
            and compact(record["frequency_rule"]["text"]) in body
            and all(compact(x) in body for x in record["potential_barriers"])
            and all(compact(x["plan"]) in body for x in record["barrier_coping_plan"]))
    plan = record.get("negotiated_record_plan") or {}
    return bool(isinstance(plan, dict) and plan.get("text") and compact(plan["text"]) in body)


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
        from .m1_contract import contract_for
        consent = contract_for(pending).get("evidence", {}).get("consent", {})
        quote = consent.get("quote")
        accepted = (isinstance(quote, str) and bool(quote) and quote in user.content
            and bool(re.search(r"愿意|开始.{0,6}目标|可以.{0,6}目标", quote))
            and not re.search(r"不|没|先别|等等|但是|不过|可是|吗|为什么|怎么|假如|如果|他说|引用|[？?“”]", user.content))
    elif module == "module_4":
        from .m4_contract import contract_for
        evidence = contract_for(pending).get("evidence", {}).get("decision_quote", {})
        accepted = evidence.get("message_id") == user.id
    else:
        previous = memory.get("dialogue_draft", {})
        accepted = (len(rows) == 3 and rows[0].role == "assistant" and affirmative(user.content)
            and previous.get("module") == module and previous.get("cycle_id") == state["active_cycle_id"]
            and previous.get("assistant_message_id") == rows[0].id
            and previous.get("fingerprint") == fingerprint(pending)
            and summary_present(module, rows[0].content, pending))
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
        marker = {"module": module, "cycle_id": state["active_cycle_id"],
            "assistant_message_id": assistant_message_id, "fingerprint": fingerprint(pending)}
        rt = schema.tables["conversation_runtime_states"]
        await db.execute(update(rt).where(rt.c.conversation_id == conversation.id).values(
            memory={**memory, "dialogue_draft": marker}))
    return None
