"""Administrator account directory and one-way role delegation."""

from __future__ import annotations

import asyncio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AccountSettings, UserAccount


@pytest.fixture
def admin_headers(register, db_sessionmaker) -> dict[str, str]:
    headers = register(username="directoryadmin", nickname="Directory Admin")

    async def promote() -> None:
        async with db_sessionmaker() as db:
            settings = (
                await db.execute(
                    select(AccountSettings)
                    .join(UserAccount, UserAccount.id == AccountSettings.account_id)
                    .where(UserAccount.username == "directoryadmin")
                )
            ).scalar_one()
            settings.role = "admin"
            await db.commit()

    asyncio.run(promote())
    return headers


def test_regular_account_cannot_list_or_grant_roles(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.get("/api/admin/accounts", headers=auth_headers).status_code == 403
    assert (
        client.patch(
            "/api/admin/accounts/1/role",
            json={"role": "admin"},
            headers=auth_headers,
        ).status_code
        == 403
    )


def test_admin_can_search_the_account_directory(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    target = client.post(
        "/api/auth/register",
        json={
            "username": "QuietRiver",
            "password": "correct-horse-battery",
            "email": "quietriver@example.com",
            "nickname": "小河",
            "tag": "24680",
            "birth_date": "1998-01-01",
        },
    ).json()["account"]

    response = client.get(
        "/api/admin/accounts", params={"query": target["username"]}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    accounts = response.json()["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["username"] == "quietriver"
    assert accounts[0]["tag"] == "24680"
    assert accounts[0]["nickname"] == "小河"
    assert accounts[0]["display_id"] == "小河#24680"
    assert accounts[0]["role"] == "user"


def test_admin_grants_role_and_existing_session_observes_it(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/auth/register",
        json={
            "username": "newmoderator",
            "password": "correct-horse-battery",
            "email": "newmoderator@example.com",
            "nickname": "New Moderator",
            "tag": "13579",
            "birth_date": "1998-01-01",
        },
    )
    target_headers = {
        "Authorization": f"Bearer {created.json()['token']}"
    }
    target_id = next(
        item["id"]
        for item in client.get(
            "/api/admin/accounts",
            params={"query": created.json()["account"]["username"]},
            headers=admin_headers,
        ).json()["accounts"]
    )

    granted = client.patch(
        f"/api/admin/accounts/{target_id}/role",
        json={"role": "admin"},
        headers=admin_headers,
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["role"] == "admin"
    assert client.get("/api/auth/me", headers=target_headers).json()["role"] == "admin"

    # Idempotent: a repeated click cannot create duplicate settings rows.
    assert (
        client.patch(
            f"/api/admin/accounts/{target_id}/role",
            json={"role": "admin"},
            headers=admin_headers,
        ).status_code
        == 200
    )


def test_grant_rejects_missing_accounts_and_demotion_payloads(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    missing = client.patch(
        "/api/admin/accounts/999999/role",
        json={"role": "admin"},
        headers=admin_headers,
    )
    assert missing.status_code == 404

    demotion = client.patch(
        "/api/admin/accounts/1/role",
        json={"role": "user"},
        headers=admin_headers,
    )
    assert demotion.status_code == 422


def test_same_nickname_can_use_distinct_user_chosen_tags(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    ids = []
    for username, tag in (("sharedone", "11111"), ("sharedtwo", "22222")):
        response = client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": "correct-horse-battery",
                "email": f"{username}@example.com",
                "nickname": "Shared Name",
                "tag": tag,
                "birth_date": "1998-01-01",
            },
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["account"]["display_id"])

    assert ids[0] != ids[1]
    listed = client.get(
        "/api/admin/accounts", params={"query": "Shared Name"}, headers=admin_headers
    )
    assert listed.status_code == 200, listed.text
    assert {item["display_id"] for item in listed.json()["accounts"]} == set(ids)
