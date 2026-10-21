"""Daily-record v2: activities and a note; day scores moved to 每日测量."""
import pytest

from app.prompts import build_system_segments
from app.schemas import AssessmentSubmission

RETIRED = ("completion_rate", "completion_not_applicable", "activity_level", "overall_mood")


def submission():
    return {
        "timezone": "Asia/Shanghai",
        "activities": [{"time_slot": "19:00–20:00", "activity": "散步", "emotion": 0}],
        "summary": {"reflection_note": None},
    }


def test_save_and_history_keep_activities_without_day_scores(client, register):
    headers = register("dailyrecord")
    body = submission()
    response = client.post("/api/assessment", headers=headers, json=body)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["scale_version"] == 2
    assert not set(RETIRED) & set(record)
    assert record["social_connection"] is None
    assert record["approach_vs_avoidance"] is None
    assert record["activities"][0]["emotion"] == 0
    for field in ("achievement", "connection", "enjoyment", "importance"):
        assert record["activities"][0][field] is None
    history = client.get("/api/assessment/history", headers=headers).json()
    assert history["items"] == [record]
    assert client.post("/api/assessment", headers=headers, json=body).status_code == 409


def test_retired_day_scores_from_an_old_client_are_ignored(client, register):
    body = submission()
    body["summary"].update(completion_rate=3, activity_level=4, overall_mood=5)
    response = client.post("/api/assessment", headers=register("oldclient"), json=body)
    assert response.status_code == 201, response.text
    assert not set(RETIRED) & set(response.json())


@pytest.mark.parametrize("field,value", [
    ("time_slot", "   "), ("activity", "   "), ("emotion", None), ("emotion", 6),
    ("achievement", -1), ("connection", 6),
])
def test_invalid_submission_is_rejected_at_api(client, register, field, value):
    body = submission()
    body["activities"][0][field] = value
    response = client.post("/api/assessment", headers=register("invaliddaily"), json=body)
    assert response.status_code == 422, response.text


def test_optional_scores_are_explicit():
    body = submission()
    body["activities"][0].update(achievement=0, connection=5, enjoyment=None, importance=3)
    parsed = AssessmentSubmission.model_validate(body)
    assert parsed.activities[0].achievement == 0
    assert parsed.activities[0].enjoyment is None


def test_note_only_is_saved_as_completed_and_preserved_in_history(client, register):
    headers = register("noteonly")
    body = submission()
    body["activities"] = []
    body["summary"]["reflection_note"] = "今天慢慢来"
    response = client.post("/api/assessment", headers=headers, json=body)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["status"] == "completed"
    assert record["activities"] == []
    assert record["reflection_note"] == "今天慢慢来"
    assert client.get("/api/assessment/history", headers=headers).json()["items"] == [record]


@pytest.mark.parametrize("note", [None, "   "])
def test_empty_record_is_rejected(client, register, note):
    body = submission()
    body["activities"] = []
    body["summary"]["reflection_note"] = note
    response = client.post("/api/assessment", headers=register("emptyday"), json=body)
    assert response.status_code == 422, response.text


def test_admin_prompt_is_not_augmented_with_a_second_daily_record_lesson():
    for module in ("module_1", "module_2", "module_3", "module_4"):
        prompt = "\n".join(s.text for s in build_system_segments(module, module_prompt="自定义提示词"))
        assert "自定义提示词" in prompt
        assert "# 每日记录的网页操作引导" not in prompt
        assert "current_module: " + module in prompt
