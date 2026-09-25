from app.workflow_readiness import (
    EXTRACTION_NOT_FRESH,
    FINGERPRINT_MISMATCH,
    M4_EVIDENCE_MISSING,
    MISSING_REQUIRED_FIELD,
    NEGOTIATED_RECORD_PLAN_MISSING,
    SUMMARY_EVIDENCE_MISSING,
    evaluate_readiness,
)


PLAN = {
    "activity_content": "晚饭后散步",
    "schedule_text": "每天晚饭后",
    "location": "小区楼下",
    "duration_minutes": 10,
    "frequency_rule": {"schema_version": 1, "text": "每天"},
    "potential_barriers": ["下雨"],
    "barrier_coping_plan": [{"barrier": "下雨", "plan": "在家走动"}],
    "difficulty_rating": 4,
    "difficulty_evidence": {"rating": {"value": 4, "message_id": 2, "quote": "4分"}},
}


def test_m2_required_fields_are_checked():
    result = evaluate_readiness("module_2", {**PLAN, "difficulty_rating": 8})
    assert not result["ready"]
    assert (MISSING_REQUIRED_FIELD, "difficulty_rating") in {
        (item.get("code"), item.get("field")) for item in result["reasons"]
    }


def test_m2_location_duration_and_frequency_are_optional():
    assert evaluate_readiness("module_2", {**PLAN, "location": None, "duration_minutes": None,
                                          "frequency_rule": None})["ready"]


def test_m3_requires_non_empty_negotiated_plan():
    result = evaluate_readiness("module_3", {
        "recording_status": "accepted",
        "record_requirement": "记录心情",
        "negotiated_record_plan": {"text": "  "},
        "feedback_mechanism": "聊天反馈",
    })
    assert not result["ready"]
    assert any(item["code"] == NEGOTIATED_RECORD_PLAN_MISSING for item in result["reasons"])
    schema_only = evaluate_readiness("module_3", {
        "recording_status": "accepted",
        "record_requirement": "记录心情",
        "negotiated_record_plan": {"schema_version": 1, "text": ""},
        "feedback_mechanism": "聊天反馈",
    })
    assert not schema_only["ready"]
    assert any(item["code"] == NEGOTIATED_RECORD_PLAN_MISSING for item in schema_only["reasons"])


def test_m4_contract_and_evidence_are_reported_separately():
    result = evaluate_readiness("module_4", {"scenario_type": "A"},
                               m4_missing_fields=["m4_milestone_2"])
    assert not result["ready"]
    assert any(item["code"] == M4_EVIDENCE_MISSING for item in result["reasons"])
    assert "review_decision" in result["missing_fields"]


def test_confirmation_markers_fail_closed_with_specific_codes():
    result = evaluate_readiness("module_2", PLAN, extraction_fresh=False,
        summary_verified=False, expected_fingerprint="old", actual_fingerprint="new",
        require_summary=True, require_fingerprint=True)
    codes = {item["code"] for item in result["reasons"]}
    assert {EXTRACTION_NOT_FRESH, SUMMARY_EVIDENCE_MISSING, FINGERPRINT_MISMATCH} <= codes
