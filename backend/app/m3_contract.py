"""Source checks for a recording decision, not a coaching-step state machine.

The extractor interprets whether a decision concerns recording and its scope.
This layer checks real roles, quotes and the current session/cycle; it does not
count explanations or judge how thoroughly the assistant has taught recording.
"""
from collections.abc import Mapping

from sqlalchemy import select

from .evidence_quotes import literal_span

VERSION = "m3-recording-20260924-v1"
RECORDING_SCOPES = {"current_arrangement", "activity_record", "daily_summary", "all_recording"}


def _present(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return _present(value.get("text"))
    return False


def _reference(raw, messages, roles):
    if not isinstance(raw, Mapping) or not isinstance(raw.get("quote"), str):
        return None
    source_id = raw.get("message_id")
    if source_id is not None and type(source_id) is not int:
        return None
    matches = []
    for message in messages:
        if message.role not in roles or (source_id is not None and source_id != message.id):
            continue
        span = literal_span(raw["quote"], message.content)
        if span:
            matches.append((message, span))
    # Repeated short confirmations need a real ID; do not guess which plan
    # the user's second or third "好的" refers to.
    if len(matches) != 1:
        return None
    message, quote = matches[0]
    return {"message_id":message.id, "position":message.position, "role":message.role, "quote":quote}


async def cycle_messages(db, messages, *, cycle_id):
    """Use this cycle's plan confirmation or authenticated continuation boundary.

    A continued cycle does not re-confirm M2. Its M4 commit is therefore the
    source boundary; an old unconfirmed M3 decision cannot be relabelled as
    consent to a new cycle. Confirmed agreements are inherited separately.
    """
    if not messages:
        return []
    from .database_v2_schema import metadata
    from .models import Conversation, ConversationMessage
    logs = metadata.tables["ai_decision_logs"]
    cycles, goals = metadata.tables["pa_cycles"], metadata.tables["pa_goals"]
    owner = (await db.execute(select(goals.c.user_id).join(cycles,
        cycles.c.goal_id == goals.c.id).where(cycles.c.id == cycle_id))).scalar_one_or_none()
    if owner is None:
        return []
    events = (await db.execute(select(logs).join(Conversation,
        Conversation.id == logs.c.conversation_id).where(
        Conversation.subject_id == owner, logs.c.module_name.in_(["module_2", "module_4"]),
        logs.c.decision_type == "user_confirmation"))).mappings().all()
    boundaries = []
    for event in events:
        value = event["decision_value"] if isinstance(event["decision_value"], dict) else {}
        belongs = ((event["module_name"] == "module_2" and event["cycle_id"] == cycle_id)
            or (event["module_name"] == "module_4" and value.get("next_cycle_id") == cycle_id
                and event["cycle_id"] != cycle_id))
        if not belongs:
            continue
        ids = event["evidence_message_ids"] if isinstance(event["evidence_message_ids"], list) else []
        # The commit's boundary can be later than the original decision it
        # audits, so prefer that current user turn when it is available.
        try:
            ids = [int(event["turn_id"])]
        except (ValueError, TypeError):
            pass
        boundaries.extend((await db.execute(select(ConversationMessage).where(
            ConversationMessage.id.in_(ids),
            ConversationMessage.conversation_id == event["conversation_id"],
            ConversationMessage.role == "user"))).scalars().all())
    def later(message, boundary):
        if message.conversation_id == boundary.conversation_id:
            return message.position > boundary.position
        return (message.created_at, message.id) > (boundary.created_at, boundary.id)
    return [message for message in messages if all(later(message, boundary) for boundary in boundaries)]


def normalize(data, messages, *, session_id, cycle_id, assistant_message_id=None,
              user_message_id=None, existing=None):
    """Return writable M3 facts plus freshly authenticated decision evidence.

    Omitted/invalid decisions remain unknown rather than preserving an earlier
    consent after a possible correction. Ordinary extraction may still retain
    descriptive fields; callers must not bypass this normalization for status.
    """
    raw = data.get("recording_evidence")
    raw = raw if isinstance(raw, Mapping) else {}
    previous = contract_for(existing)
    previous_evidence = (previous.get("evidence") or {}) if (
        previous.get("session_id") == session_id and previous.get("cycle_id") == cycle_id
    ) else {}
    evidence = {}
    for key, roles in {"requirement":{"assistant"}, "decision":{"user"},
                       "plan":{"assistant", "user"}, "feedback":{"assistant"},
                       "limitations":{"assistant"}}.items():
        candidate = raw.get(key)
        if key != "decision" and key not in raw:
            # Keep sourced descriptive facts if an extraction omitted them.
            # Never carry an old acceptance/refusal through an omitted decision.
            candidate = previous_evidence.get(key)
        reference = _reference(candidate, messages, roles)
        if reference:
            evidence[key] = reference

    decision = raw.get("decision") if isinstance(raw.get("decision"), Mapping) else {}
    requested_status = data.get("recording_status")
    scope = decision.get("scope")
    status = "unknown"
    if (requested_status in {"accepted", "declined"} and decision.get("status") == requested_status
            and scope in RECORDING_SCOPES and "decision" in evidence):
        status = requested_status
        evidence["decision"].update(status=status, scope=scope)
    else:
        evidence.pop("decision", None)

    # A user's consent cannot confirm a recording plan proposed afterwards.
    # This is source/version ordering, not a required conversational sequence.
    if status == "accepted" and "plan" in evidence and "decision" in evidence:
        if evidence["plan"]["position"] > evidence["decision"]["position"]:
            status = "unknown"
            evidence.pop("decision", None)

    # A completion from a stale/synthetic extraction boundary must never gain
    # authority. The caller supplies owned messages read from the database.
    boundary_id = user_message_id if user_message_id is not None else assistant_message_id
    boundary_role = "user" if user_message_id is not None else "assistant"
    boundary_valid = bool(messages and messages[-1].id == boundary_id
                          and messages[-1].role == boundary_role and session_id and cycle_id)
    if not boundary_valid:
        evidence = {}
        status = "unknown"

    requirement = evidence.get("requirement", {}).get("quote")
    plan = evidence.get("plan", {}).get("quote")
    feedback = evidence.get("feedback", {}).get("quote")
    if status == "declined":
        # Keep the refusal's concrete scope in evidence. Do not manufacture an
        # accepted activity-recording plan or cancel unrelated daily summaries.
        plan = None
        evidence.pop("plan", None)
    contract = {"version":VERSION, "session_id":session_id, "cycle_id":cycle_id,
        "assistant_message_id":assistant_message_id, "user_message_id":user_message_id,
        "source_message_id":boundary_id, "evidence":evidence,
        "recording_status":status, "verified":boundary_valid}
    values = {
        "recording_status":status, "recording_evidence":contract,
        "record_requirement":requirement,
        "negotiated_record_plan":{"schema_version":1, "text":plan} if plan else None,
        "feedback_mechanism":feedback,
    }
    # These describe the user's attitude; they never become recording_status.
    if "user_acceptance_level" in data:
        level = data["user_acceptance_level"]
        values["acceptance_status"] = str(level) if type(level) is int and level in {0, 1, 2} else None
    if "user_acceptance_feeling" in data:
        values["acceptance_feeling"] = data["user_acceptance_feeling"]
    return values


def contract_for(record):
    value = (record or {}).get("recording_evidence")
    return value if isinstance(value, Mapping) and value.get("version") == VERSION else {}


def missing_fields(record, *, session_id=None, cycle_id=None):
    record = record or {}
    status = record.get("recording_status")
    missing = []
    if status not in {"accepted", "declined"}:
        missing.append("recording_status")
    if not _present(record.get("feedback_mechanism")):
        missing.append("feedback_mechanism")
    if status == "accepted":
        if not _present(record.get("record_requirement")):
            missing.append("record_requirement")
        if not _present(record.get("negotiated_record_plan")):
            missing.append("negotiated_record_plan")
    contract = contract_for(record)
    valid = (contract.get("verified") is True and contract.get("recording_status") == status
        and (session_id is None or contract.get("session_id") == session_id)
        and (cycle_id is None or contract.get("cycle_id") == cycle_id))
    evidence = contract.get("evidence") if valid and isinstance(contract.get("evidence"), Mapping) else {}
    decision = evidence.get("decision") or {}
    if (decision.get("role") != "user" or decision.get("status") != status
            or decision.get("scope") not in RECORDING_SCOPES or not _present(decision.get("quote"))):
        missing.append("recording_decision_evidence")
    for key in ({"requirement", "plan", "feedback"} if status == "accepted"
                else {"feedback", "limitations"} if status == "declined" else set()):
        source = evidence.get(key) or {}
        roles = {"assistant", "user"} if key == "plan" else {"assistant"}
        if source.get("role") not in roles or not _present(source.get("quote")):
            missing.append(f"recording_{key}_evidence")
        field = {"requirement":"record_requirement", "plan":"negotiated_record_plan",
                 "feedback":"feedback_mechanism"}.get(key)
        value = record.get(field) if field else None
        if isinstance(value, Mapping):
            value = value.get("text")
        if field and value != source.get("quote"):
            missing.append(f"recording_{key}_evidence")
    return sorted(set(missing))
