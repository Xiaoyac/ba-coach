"""Daily-record v2: explicit ratings, optional scores and historical fidelity."""
from copy import deepcopy

import pytest

from app.prompts import build_system_segments
from app.schemas import AssessmentSubmission


def submission():
    return {
        "timezone": "Asia/Shanghai",
        "activities": [{"time_slot": "19:00–20:00", "activity": "散步", "emotion": 0}],
        "summary": {"completion_rate": 0, "activity_level": 5, "overall_mood": 0},
    }


@pytest.mark.parametrize("no_plan", [False, True])
def test_save_and_history_preserve_zero_null_and_no_plan(client, register, no_plan):
    headers = register("dailyrecord")
    body = submission()
    if no_plan:
        body["summary"].update(completion_rate=None, completion_not_applicable=True)
    response = client.post("/api/assessment", headers=headers, json=body)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["scale_version"] == 2
    assert record["completion_not_applicable"] is no_plan
    assert record["completion_rate"] == (None if no_plan else 0)
    assert record["activity_level"] == 5
    assert record["overall_mood"] == 0
    assert record["social_connection"] is None
    assert record["approach_vs_avoidance"] is None
    assert record["activities"][0]["emotion"] == 0
    for field in ("achievement", "connection", "enjoyment", "importance"):
        assert record["activities"][0][field] is None
    history = client.get("/api/assessment/history", headers=headers).json()
    assert history["items"] == [record]
    assert client.post("/api/assessment", headers=headers, json=body).status_code == 409


@pytest.mark.parametrize("scope,field,value", [
    ("activity", "time_slot", "   "), ("activity", "activity", "   "),
    ("activity", "emotion", None), ("activity", "emotion", 6),
    ("activity", "achievement", -1), ("activity", "connection", 6),
    ("summary", "completion_rate", None), ("summary", "completion_rate", 6),
    ("summary", "completion_not_applicable", True),
    ("summary", "activity_level", None), ("summary", "overall_mood", 6),
])
def test_invalid_submission_is_rejected_at_api(client, register, scope, field, value):
    body = submission()
    target = body["activities"][0] if scope == "activity" else body["summary"]
    target[field] = value
    response = client.post("/api/assessment", headers=register("invaliddaily"), json=body)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("field", ["completion_rate", "activity_level", "overall_mood"])
def test_summary_has_no_implicit_scores(field):
    body = submission()
    del body["summary"][field]
    with pytest.raises(ValueError):
        AssessmentSubmission.model_validate(body)


def test_optional_scores_are_explicit_and_empty_activity_not_accepted():
    body = submission()
    body["activities"][0].update(achievement=0, connection=5, enjoyment=None, importance=3)
    parsed = AssessmentSubmission.model_validate(body)
    assert parsed.activities[0].achievement == 0
    assert parsed.activities[0].enjoyment is None
    invalid = deepcopy(body)
    invalid["activities"] = []
    with pytest.raises(ValueError):
        AssessmentSubmission.model_validate(invalid)


def test_m3_instruction_survives_admin_override_without_affecting_other_modules():
    for module in ("module_1", "module_2", "module_3", "module_4"):
        prompt = "\n".join(s.text for s in build_system_segments(module, module_prompt="自定义提示词"))
        assert "自定义提示词" in prompt
        assert ("# 每日记录的网页操作引导" in prompt) == (module == "module_3")
        if module == "module_3":
            for text in ("记录今日", "打开每日记录", "保存今日记录", "不等于0分", "可选", "不把日记表单当作"):
                assert text in prompt
