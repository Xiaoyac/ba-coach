"""User problem reports and administrator-only screenshot access."""

from __future__ import annotations

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AccountSettings, UserAccount


@pytest.fixture
def report_admin_headers(register, db_sessionmaker) -> dict[str, str]:
    headers = register(username="reportadmin", nickname="Report Admin", tag="90817")

    async def promote() -> None:
        async with db_sessionmaker() as db:
            settings = (
                await db.execute(
                    select(AccountSettings)
                    .join(UserAccount, UserAccount.id == AccountSettings.account_id)
                    .where(UserAccount.username == "reportadmin")
                )
            ).scalar_one()
            settings.role = "admin"
            await db.commit()

    asyncio.run(promote())
    return headers


def _payload(*, screenshot: bool = True) -> dict:
    # The route validates MIME, base64 and the actual file signature. Keeping
    # this tiny makes the test assert binary round-tripping without carrying a
    # large fixture in source control.
    png = b"\x89PNG\r\n\x1a\n" + b"browser-capture"
    return {
        "description": "点击发送后一直没有看到回复",
        "screenshot_data_url": (
            "data:image/png;base64," + base64.b64encode(png).decode()
            if screenshot
            else None
        ),
        "page_url": "https://bacoach.xyz/",
        "session_id": "session-for-report",
        "last_error": "Failed to fetch",
        "user_agent": "BA Coach browser test",
        "viewport_width": 390,
        "viewport_height": 844,
        "client_online": True,
    }


def test_report_requires_authentication(client: TestClient) -> None:
    assert client.post("/api/issue-reports", json=_payload()).status_code == 401


def test_user_submits_report_and_only_admin_reads_screenshot(
    client: TestClient,
    auth_headers: dict[str, str],
    report_admin_headers: dict[str, str],
) -> None:
    created = client.post("/api/issue-reports", json=_payload(), headers=auth_headers)
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]

    assert client.get("/api/admin/issue-reports", headers=auth_headers).status_code == 403

    listed = client.get(
        "/api/admin/issue-reports", params={"status": "open"}, headers=report_admin_headers
    )
    assert listed.status_code == 200, listed.text
    report = listed.json()["reports"][0]
    assert report["id"] == report_id
    assert report["has_screenshot"] is True
    assert report["description"] == _payload()["description"]
    assert "screenshot_data_url" not in report

    screenshot = client.get(
        f"/api/admin/issue-reports/{report_id}/screenshot",
        headers=report_admin_headers,
    )
    assert screenshot.status_code == 200
    assert screenshot.headers["content-type"] == "image/png"
    assert screenshot.content.startswith(b"\x89PNG")

    resolved = client.patch(
        f"/api/admin/issue-reports/{report_id}/status",
        json={"status": "resolved"},
        headers=report_admin_headers,
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolved_at"] is not None


def test_screenshot_validation_and_text_only_fallback(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    invalid = _payload()
    invalid["screenshot_data_url"] = "data:text/html;base64,PGgxPm5vPC9oMT4="
    assert (
        client.post("/api/issue-reports", json=invalid, headers=auth_headers).status_code
        == 422
    )

    text_only = client.post(
        "/api/issue-reports", json=_payload(screenshot=False), headers=auth_headers
    )
    assert text_only.status_code == 201, text_only.text
