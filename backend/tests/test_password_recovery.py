"""Email collection, verification, and one-time password recovery."""

from __future__ import annotations

import asyncio
import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AccountEmail, AccountEmailToken
from app.routes import auth as auth_route


def _registration(**overrides) -> dict:
    data = {
        "username": "recoverme",
        "password": "correct-horse-battery",
        "email": "recoverme@example.com",
        "nickname": "小河",
        "tag": "48291",
        "birth_date": "1998-01-01",
    }
    data.update(overrides)
    return data


def _capture_mail(monkeypatch) -> list[dict[str, str]]:
    sent: list[dict[str, str]] = []

    async def fake_send(**message: str) -> None:
        sent.append(message)

    monkeypatch.setattr(auth_route, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(auth_route, "send_account_email", fake_send)
    return sent


def _token(message: dict[str, str], path: str) -> str:
    match = re.search(rf"/{path}\?token=([^\s]+)", message["text"])
    assert match, message["text"]
    return match.group(1)


def test_registration_requires_email(client: TestClient) -> None:
    payload = _registration()
    payload.pop("email")
    assert client.post("/api/auth/register", json=payload).status_code == 422


def test_register_verify_forgot_and_reset_is_single_use(
    client: TestClient, monkeypatch
) -> None:
    sent = _capture_mail(monkeypatch)
    registered = client.post("/api/auth/register", json=_registration())
    assert registered.status_code == 201, registered.text
    account = registered.json()["account"]
    assert account["email"] == "recoverme@example.com"
    assert account["email_required"] is False
    assert account["email_verified"] is False
    assert len(sent) == 1

    verify_token = _token(sent[0], "verify-email")
    verified = client.post("/api/auth/email/verify", json={"token": verify_token})
    assert verified.status_code == 200, verified.text
    # Verification links are single-use.
    assert client.post(
        "/api/auth/email/verify", json={"token": verify_token}
    ).status_code == 400

    forgot = client.post(
        "/api/auth/password/forgot", json={"email": " RECOVERME@example.com "}
    )
    assert forgot.status_code == 202
    reset_token = _token(sent[-1], "reset-password")
    reset = client.post(
        "/api/auth/password/reset",
        json={"token": reset_token, "new_password": "a-new-safe-password"},
    )
    assert reset.status_code == 200, reset.text

    old_session = registered.json()["token"]
    assert client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {old_session}"}
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "recoverme", "password": "correct-horse-battery"},
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "recoverme", "password": "a-new-safe-password"},
    ).status_code == 200
    assert client.post(
        "/api/auth/password/reset",
        json={"token": reset_token, "new_password": "cannot-reuse-this"},
    ).status_code == 400


def test_forgot_response_does_not_reveal_unknown_or_unverified_email(
    client: TestClient, monkeypatch
) -> None:
    sent = _capture_mail(monkeypatch)
    assert client.post("/api/auth/register", json=_registration()).status_code == 201
    sent.clear()
    unknown = client.post(
        "/api/auth/password/forgot", json={"email": "ghost@example.com"}
    )
    unverified = client.post(
        "/api/auth/password/forgot", json={"email": "recoverme@example.com"}
    )
    assert unknown.status_code == unverified.status_code == 202
    assert unknown.json() == unverified.json()
    assert sent == []


def test_legacy_account_is_prompted_until_it_adds_email(
    client: TestClient, db_sessionmaker, monkeypatch
) -> None:
    sent = _capture_mail(monkeypatch)
    registered = client.post("/api/auth/register", json=_registration()).json()

    async def remove_email() -> None:
        async with db_sessionmaker() as db:
            for row in (await db.execute(select(AccountEmailToken))).scalars().all():
                await db.delete(row)
            email = (await db.execute(select(AccountEmail))).scalar_one()
            await db.delete(email)
            await db.commit()

    asyncio.get_event_loop().run_until_complete(remove_email())
    headers = {"Authorization": f"Bearer {registered['token']}"}
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] is None
    assert me.json()["email_required"] is True

    sent.clear()
    saved = client.put(
        "/api/auth/email", json={"email": "legacy@example.com"}, headers=headers
    )
    assert saved.status_code == 202, saved.text
    assert saved.json()["email_required"] is False
    assert saved.json()["email_verified"] is False
    assert len(sent) == 1


def test_email_address_is_globally_unique(client: TestClient) -> None:
    assert client.post("/api/auth/register", json=_registration()).status_code == 201
    second = client.post(
        "/api/auth/register",
        json=_registration(
            username="anotheruser", nickname="另一个", tag="59302",
            email="RECOVERME@example.com",
        ),
    )
    assert second.status_code == 409
