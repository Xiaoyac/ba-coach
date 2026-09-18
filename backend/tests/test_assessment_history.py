"""History reads for the Daily Behavioral Activation Assessment."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.models import (
    STATUS_COMPLETED,
    STATUS_SKIPPED,
    ActivityLog,
    AssessmentEntry,
)


def _profile_uuid(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200
    return response.json()["profile_uuid"]


def _seed_history(db_sessionmaker, *entries: AssessmentEntry) -> None:
    async def seed() -> None:
        async with db_sessionmaker() as db:
            db.add_all(entries)
            await db.commit()

    asyncio.get_event_loop().run_until_complete(seed())


def _completed(subject_id: str, on: date, activity: str) -> AssessmentEntry:
    entry = AssessmentEntry(
        subject_id=subject_id,
        recorded_on=on,
        timezone="Asia/Shanghai",
        status=STATUS_COMPLETED,
        completion_rate=7,
        activity_level=6,
        social_connection=5,
        approach_vs_avoidance=8,
        overall_mood=7,
        reflection_note=f"{activity}之后感觉不错",
    )
    # Insert out of display order to prove the relationship's position order
    # survives the history endpoint.
    entry.activities.extend(
        [
            ActivityLog(
                position=1,
                time_slot="20:00–21:00",
                activity=f"{activity}后的拉伸",
                emotion=4,
                achievement=3,
                connection=2,
                enjoyment=4,
                importance=3,
            ),
            ActivityLog(
                position=0,
                time_slot="19:00–20:00",
                activity=activity,
                emotion=4,
                achievement=4,
                connection=3,
                enjoyment=5,
                importance=4,
            ),
        ]
    )
    return entry


def test_history_is_newest_first_and_includes_ordered_activities(
    client: TestClient, register, db_sessionmaker
) -> None:
    headers = register("historyowner")
    subject_id = _profile_uuid(client, headers)
    today = date.today()
    _seed_history(
        db_sessionmaker,
        _completed(subject_id, today - timedelta(days=2), "散步"),
        _completed(subject_id, today, "打羽毛球"),
    )

    response = client.get("/api/assessment/history", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert [item["local_date"] for item in body["items"]] == [
        today.isoformat(),
        (today - timedelta(days=2)).isoformat(),
    ]
    assert [activity["activity"] for activity in body["items"][0]["activities"]] == [
        "打羽毛球",
        "打羽毛球后的拉伸",
    ]
    assert body["has_more"] is False
    assert body["next_offset"] is None
    assert body["items"][0]["scale_version"] == 1
    assert body["items"][0]["completion_rate"] == 7
    assert body["items"][0]["activity_level"] == 6
    assert body["items"][0]["completion_not_applicable"] is False


def test_history_is_account_scoped_and_omits_skipped_days(
    client: TestClient, register, db_sessionmaker
) -> None:
    owner_headers = register("historyowner")
    other_headers = register("historyother")
    owner_id = _profile_uuid(client, owner_headers)
    other_id = _profile_uuid(client, other_headers)
    today = date.today()
    _seed_history(
        db_sessionmaker,
        _completed(owner_id, today, "瑜伽"),
        AssessmentEntry(
            subject_id=owner_id,
            recorded_on=today - timedelta(days=1),
            timezone="Asia/Shanghai",
            status=STATUS_SKIPPED,
        ),
        _completed(other_id, today, "游泳"),
    )

    owner = client.get("/api/assessment/history", headers=owner_headers).json()
    other = client.get("/api/assessment/history", headers=other_headers).json()

    assert [item["activities"][0]["activity"] for item in owner["items"]] == ["瑜伽"]
    assert [item["activities"][0]["activity"] for item in other["items"]] == ["游泳"]


def test_history_paginates_without_repeating_rows(
    client: TestClient, register, db_sessionmaker
) -> None:
    headers = register("historypages")
    subject_id = _profile_uuid(client, headers)
    today = date.today()
    _seed_history(
        db_sessionmaker,
        *[
            _completed(subject_id, today - timedelta(days=index), f"活动 {index}")
            for index in range(3)
        ],
    )

    first = client.get(
        "/api/assessment/history?limit=2", headers=headers
    ).json()
    second = client.get(
        f"/api/assessment/history?limit=2&offset={first['next_offset']}",
        headers=headers,
    ).json()

    assert len(first["items"]) == 2
    assert first["has_more"] is True
    assert first["next_offset"] == 2
    assert len(second["items"]) == 1
    assert second["has_more"] is False
    assert {item["id"] for item in first["items"]}.isdisjoint(
        {item["id"] for item in second["items"]}
    )


def test_history_requires_login(client: TestClient) -> None:
    response = client.get("/api/assessment/history")
    assert response.status_code == 401
