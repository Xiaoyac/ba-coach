"""Administrator account directory and one-way role delegation."""

from __future__ import annotations

from invitation_helpers import with_test_invitation

import asyncio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AccountSettings, Conversation, ConversationMessage, UserAccount


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
        json=with_test_invitation(client, {
            "username": "QuietRiver",
            "password": "correct-horse-battery",
            "email": "quietriver@example.com",
            "nickname": "小河",
            "tag": "24680",
            "birth_year": 1998,
        }),
    ).json()["account"]

    response = client.get(
        "/api/admin/accounts", params={"query": target["username"]}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    accounts = response.json()["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["username"] == "quietriver"
    assert "tag" not in accounts[0] and "display_id" not in accounts[0]
    assert accounts[0]["nickname"] == "小河"
    assert accounts[0]["role"] == "user"


def test_admin_grants_role_and_existing_session_observes_it(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/auth/register",
        json=with_test_invitation(client, {
            "username": "newmoderator",
            "password": "correct-horse-battery",
            "email": "newmoderator@example.com",
            "nickname": "New Moderator",
            "tag": "13579",
            "birth_year": 1998,
        }),
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


def test_admin_can_delete_another_account_and_cannot_delete_self(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/auth/register",
        json=with_test_invitation(client, {
            "username": "deletecandidate",
            "password": "correct-horse-battery",
            "email": "deletecandidate@example.com",
            "nickname": "待删除用户",
            "tag": "81023",
            "birth_year": 1998,
        }),
    )
    assert created.status_code == 201, created.text
    target_id = next(
        item["id"] for item in client.get(
            "/api/admin/accounts", params={"query": "deletecandidate"}, headers=admin_headers
        ).json()["accounts"]
    )
    wrong = client.request("DELETE", f"/api/admin/accounts/{target_id}",
                           headers=admin_headers, json={"password": "not-my-password"})
    assert wrong.status_code == 403
    assert client.delete(f"/api/admin/accounts/{target_id}", headers=admin_headers).status_code == 422
    deleted = client.request("DELETE", f"/api/admin/accounts/{target_id}",
                             headers=admin_headers, json={"password": "correct-horse-battery"})
    assert deleted.status_code == 204, deleted.text
    assert client.get(
        "/api/admin/accounts", params={"query": "deletecandidate"}, headers=admin_headers
    ).json()["accounts"] == []
    assert client.request("DELETE", "/api/admin/accounts/999999", headers=admin_headers,
                          json={"password": "bacoach_delete"}).status_code == 404

    # The route is explicitly destructive but an administrator cannot revoke
    # the session that is authorizing the request.
    current_id = next(
        item["id"] for item in client.get(
            "/api/admin/accounts", params={"query": "directoryadmin"}, headers=admin_headers
        ).json()["accounts"]
    )
    self_delete = client.request("DELETE", f"/api/admin/accounts/{current_id}",
                                 headers=admin_headers, json={"password": "bacoach_delete"})
    assert self_delete.status_code == 409


def test_regular_account_cannot_delete_an_account(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.request("DELETE", "/api/admin/accounts/1", headers=auth_headers,
                          json={"password": "bacoach_delete"}).status_code == 403


def test_same_nickname_can_use_distinct_user_chosen_tags(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    ids = []
    for username, tag in (("sharedone", "11111"), ("sharedtwo", "22222")):
        response = client.post(
            "/api/auth/register",
            json=with_test_invitation(client, {
                "username": username,
                "password": "correct-horse-battery",
                "email": f"{username}@example.com",
                "nickname": "Shared Name",
                "tag": tag,
                "birth_year": 1998,
            }),
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["account"]["username"])

    assert ids[0] != ids[1]
    listed = client.get(
        "/api/admin/accounts", params={"query": "Shared Name"}, headers=admin_headers
    )
    assert listed.status_code == 200, listed.text
    assert {item["username"] for item in listed.json()["accounts"]} == set(ids)


def test_staying_signed_in_moves_last_active_but_not_last_login(
    client: TestClient, admin_headers: dict[str, str], auth_headers: dict[str, str], db_sessionmaker
) -> None:
    from datetime import datetime, timedelta, timezone

    async def backdate() -> None:
        async with db_sessionmaker() as db:
            old = datetime.now(timezone.utc) - timedelta(days=1)
            for account in (await db.execute(select(UserAccount))).scalars():
                account.last_login_at = old
                account.last_seen_at = old
            await db.commit()

    asyncio.run(backdate())
    assert client.get("/api/auth/me", headers=auth_headers).status_code == 200

    accounts = client.get("/api/admin/accounts", headers=admin_headers).json()["accounts"]
    member = next(a for a in accounts if a["role"] == "user")
    login = datetime.fromisoformat(member["last_login_at"])
    active = datetime.fromisoformat(member["last_active_at"])
    assert active - login > timedelta(hours=23)


def test_admin_reads_another_accounts_chats_read_only(
    client: TestClient, admin_headers: dict[str, str], register, db_sessionmaker
) -> None:
    member = register(username="chatreader", nickname="被查看用户")
    account_id = next(item["id"] for item in client.get(
        "/api/admin/accounts", params={"query": "chatreader"}, headers=admin_headers
    ).json()["accounts"])

    async def seed() -> None:
        async with db_sessionmaker() as db:
            profile = (await db.execute(select(UserAccount.profile_uuid).where(
                UserAccount.username == "chatreader"))).scalar_one()
            chat = Conversation(subject_id=profile, session_id="admin-read-1", title="最近有点累")
            chat.messages = [
                ConversationMessage(position=0, role="user", content="最近总是提不起劲"),
                ConversationMessage(position=1, role="assistant", content="听起来这段时间不轻松。"),
            ]
            db.add_all([chat, Conversation(subject_id="someone-else", session_id="other-1", title="别人的")])
            await db.commit()
    asyncio.run(seed())

    listed = client.get(f"/api/admin/accounts/{account_id}/conversations", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["account"]["username"] == "chatreader"
    # Only this account's chat, with its whole transcript; the other subject's is absent.
    assert [c["session_id"] for c in body["conversations"]] == ["admin-read-1"]
    assert [m["content"] for m in body["conversations"][0]["messages"]] == [
        "最近总是提不起劲", "听起来这段时间不轻松。"]
    assert client.get("/api/admin/accounts/999999/conversations", headers=admin_headers).status_code == 404
    # Members cannot read anyone's history, their own included, through this route.
    assert client.get(f"/api/admin/accounts/{account_id}/conversations", headers=member).status_code == 403
