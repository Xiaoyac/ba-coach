"""Deterministic readiness checks shared by workflow boundaries.

The router may provide evidence for a transition, but it does not decide
whether a record is ready to commit.  This module deliberately has no
database or LLM dependencies.  Callers pass the current record and the
evidence markers they have already verified, and receive stable reason codes
that can be shown in diagnostics or returned by an API.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# Keep these values stable: they are persisted in diagnostics and consumed by
# the UI/tests.  Human-readable text belongs in the ``message`` field below.
MISSING_RECORD = "record_missing"
MISSING_REQUIRED_FIELD = "missing_required_field"
NEGOTIATED_RECORD_PLAN_MISSING = "negotiated_record_plan_missing"
M4_EVIDENCE_MISSING = "m4_evidence_missing"
EXTRACTION_NOT_FRESH = "extraction_not_fresh"
SUMMARY_EVIDENCE_MISSING = "summary_evidence_missing"
FINGERPRINT_MISSING = "fingerprint_missing"
FINGERPRINT_MISMATCH = "fingerprint_mismatch"
EVIDENCE_VERSION_MISMATCH = "evidence_version_mismatch"
CONFIRMATION_WINDOW_CLOSED = "confirmation_window_closed"
REVIEW_ACTION_MISSING = "review_action_missing"

# Readable aliases for integrations that name the field rather than the
# validation event.  Keep the canonical values above for persisted logs.
MISSING_NEGOTIATED_RECORD_PLAN = NEGOTIATED_RECORD_PLAN_MISSING
EXTRACTION_FRESHNESS_MISSING = EXTRACTION_NOT_FRESH


_REASON_MESSAGES = {
    MISSING_RECORD: "当前模块没有可确认的草稿",
    MISSING_REQUIRED_FIELD: "记录仍缺少必填字段",
    NEGOTIATED_RECORD_PLAN_MISSING: "记录办法尚未明确",
    M4_EVIDENCE_MISSING: "本轮复盘证据尚未完整",
    EXTRACTION_NOT_FRESH: "最新一轮讨论尚未成功刷新记录",
    SUMMARY_EVIDENCE_MISSING: "用户尚未看到与当前草稿对应的完整摘要",
    FINGERPRINT_MISSING: "当前草稿没有可核对的确认版本",
    FINGERPRINT_MISMATCH: "用户确认的版本已发生变化",
    EVIDENCE_VERSION_MISMATCH: "证据不属于当前会话或周期",
    CONFIRMATION_WINDOW_CLOSED: "当前模块尚未进入可确认状态",
    REVIEW_ACTION_MISSING: "复盘方向缺少聊天中的真实决定证据",
}


def _present(value: Any) -> bool:
    """Whether a value contains usable content (without inferring meaning)."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        # M3's negotiated plan is normally {text, ...}.  Its schema marker is
        # metadata rather than plan content, so an object such as
        # {"schema_version": 1, "text": ""} must remain empty.
        if "text" in value:
            return _present(value.get("text"))
        # Other JSON objects are considered present when they contain at
        # least one usable value (for example an M4 contract object).
        return any(_present(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return bool(value) and all(_present(item) for item in value)
    return True


def _reason(code: str, *, field: str | None = None, detail: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "message": _REASON_MESSAGES.get(code, code),
    }
    if field:
        result["field"] = field
    if detail is not None:
        result["detail"] = detail
    return result


def _missing_m2_fields(record: dict[str, Any]) -> list[str]:
    # Keep the canonical shape check in plan_contract.py.  Supplying defaults
    # here turns partially populated model output into structured reasons
    # instead of a KeyError.
    from .plan_contract import missing_plan_fields

    required = {
        "activity_content": record.get("activity_content"),
        "schedule_text": record.get("schedule_text"),
        "location": record.get("location"),
        "duration_minutes": record.get("duration_minutes"),
        "frequency_rule": record.get("frequency_rule"),
        "potential_barriers": record.get("potential_barriers"),
        "barrier_coping_plan": record.get("barrier_coping_plan"),
    }
    return missing_plan_fields(required)


def _m4_missing_fields(record: Mapping[str, Any], supplied: Any) -> list[str]:
    if supplied is not None:
        if isinstance(supplied, str):
            return [supplied] if supplied else []
        return [str(item) for item in supplied if item]
    phase_c = record.get("phase_c")
    contract = phase_c.get("_m4_contract") if isinstance(phase_c, dict) else None
    if not isinstance(contract, dict):
        return ["m4_evidence_refresh"]
    return [str(item) for item in contract.get("missing_fields", []) if item]


def evaluate_readiness(
    module: str,
    record: Mapping[str, Any] | None,
    *,
    extraction_fresh: bool | None = None,
    summary_verified: bool | None = None,
    expected_fingerprint: str | None = None,
    actual_fingerprint: str | None = None,
    evidence_session_id: str | None = None,
    session_id: str | None = None,
    evidence_cycle_id: Any = None,
    cycle_id: Any = None,
    m4_missing_fields: list[str] | tuple[str, ...] | None = None,
    require_summary: bool = False,
    require_fingerprint: bool = False,
) -> dict[str, Any]:
    """Return the single deterministic readiness result for a record.

    ``None`` means that a caller has no marker to evaluate.  It is not treated
    as a successful marker: callers that require summary/fingerprint evidence
    should set the corresponding ``require_*`` flag.  The result is JSON-safe:
    ``ready`` is a boolean, ``missing_fields`` is a flat list and ``reasons``
    contains stable ``code`` values plus human-readable context.
    """
    reasons: list[dict[str, Any]] = []
    missing_fields: list[str] = []
    if module not in {"module_2", "module_3", "module_4"}:
        return {"ready": record is not None, "module": module,
                "missing_fields": [], "reasons": reasons}
    if not isinstance(record, Mapping):
        reasons.append(_reason(MISSING_RECORD))
    else:
        if module == "module_2":
            missing_fields.extend(_missing_m2_fields(record))
        elif module == "module_3":
            for field in ("record_requirement", "feedback_mechanism"):
                if not _present(record.get(field)):
                    missing_fields.append(field)
            if not _present(record.get("negotiated_record_plan")):
                # Keep a dedicated code: this was historically collapsed into
                # a generic missing field and made M3 failures hard to explain.
                reasons.append(_reason(NEGOTIATED_RECORD_PLAN_MISSING,
                                       field="negotiated_record_plan"))
                missing_fields.append("negotiated_record_plan")
        else:
            missing_fields.extend(_m4_missing_fields(record, m4_missing_fields))
            if missing_fields:
                reasons.append(_reason(M4_EVIDENCE_MISSING,
                                       detail=list(dict.fromkeys(missing_fields))))
            if record.get("scenario_type") not in {"A", "B", "C"}:
                missing_fields.append("scenario_type")
            if record.get("chain_confirmation_status") != "confirmed" or not record.get("confirmation_message_id"):
                missing_fields.append("chain_confirmation_status")
            if record.get("review_decision") not in {1, 2, 3, 4}:
                missing_fields.append("review_decision")

    # M2/M3/M4 all use source-validated extraction markers.  The caller can
    # omit this check while constructing a draft, but confirmation paths set
    # extraction_fresh explicitly and therefore fail closed.
    if extraction_fresh is False:
        reasons.append(_reason(EXTRACTION_NOT_FRESH))

    if require_summary or summary_verified is not None:
        if summary_verified is not True:
            reasons.append(_reason(SUMMARY_EVIDENCE_MISSING))

    if require_fingerprint or expected_fingerprint is not None or actual_fingerprint is not None:
        if not expected_fingerprint or not actual_fingerprint:
            reasons.append(_reason(FINGERPRINT_MISSING))
        elif expected_fingerprint != actual_fingerprint:
            reasons.append(_reason(FINGERPRINT_MISMATCH,
                                   detail={"expected": expected_fingerprint,
                                           "actual": actual_fingerprint}))

    if evidence_session_id is not None and session_id is not None and evidence_session_id != session_id:
        reasons.append(_reason(EVIDENCE_VERSION_MISMATCH, field="session_id"))
    if evidence_cycle_id is not None and cycle_id is not None and evidence_cycle_id != cycle_id:
        reasons.append(_reason(EVIDENCE_VERSION_MISMATCH, field="cycle_id"))

    for field in dict.fromkeys(missing_fields):
        # negotiated_record_plan already has its specific code above.
        if field == "negotiated_record_plan" and any(
            item["code"] == NEGOTIATED_RECORD_PLAN_MISSING for item in reasons
        ):
            continue
        reasons.append(_reason(MISSING_REQUIRED_FIELD, field=field))

    # A record is ready only when its shape and every supplied evidence marker
    # pass.  Do not make a missing optional marker fail draft display unless the
    # caller explicitly requested that marker.
    return {
        "ready": not reasons,
        "module": module,
        "missing_fields": list(dict.fromkeys(missing_fields)),
        "reasons": reasons,
    }


# Names used by callers that prefer a predicate/check vocabulary.
check_readiness = evaluate_readiness
assess_readiness = evaluate_readiness


__all__ = [
    "evaluate_readiness", "check_readiness", "assess_readiness",
    "MISSING_RECORD", "MISSING_REQUIRED_FIELD", "NEGOTIATED_RECORD_PLAN_MISSING",
    "M4_EVIDENCE_MISSING", "EXTRACTION_NOT_FRESH", "SUMMARY_EVIDENCE_MISSING",
    "FINGERPRINT_MISSING", "FINGERPRINT_MISMATCH", "EVIDENCE_VERSION_MISMATCH",
    "CONFIRMATION_WINDOW_CLOSED", "REVIEW_ACTION_MISSING",
    "MISSING_NEGOTIATED_RECORD_PLAN", "EXTRACTION_FRESHNESS_MISSING",
]
