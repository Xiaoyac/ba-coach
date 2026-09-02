"""Administrator module sandbox: permissions, reset semantics, isolation."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AccountSettings, Conversation, ConversationRuntimeState, UserAccount
from app.models_business import UserProfile


@pytest.fixture
def sandbox_admin_headers(register, db_sessionmaker) -> dict[str, str]:
    headers = register(username="sandboxadmin", nickname="Sandbox Admin")

    async def promote() -> None:
        async with db_sessionmaker() as db:
            settings = (
                await db.execute(
                    select(AccountSettings)
                    .join(UserAccount, UserAccount.id == AccountSettings.account_id)
                    .where(UserAccount.username == "sandboxadmin")
                )
            ).scalar_one()
            settings.role = "admin"
            await db.commit()

    asyncio.run(promote())
    return headers


def test_regular_account_cannot_start_module_sandbox(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/admin/sandbox/module",
        json={"module": "module_2"},
        headers=auth_headers,
    )
    assert response.status_code == 403


def test_each_selection_starts_a_fresh_isolated_module(
    client: TestClient,
    sandbox_admin_headers: dict[str, str],
    db_sessionmaker,
    store,
) -> None:
    first = client.post(
        "/api/admin/sandbox/module",
        json={"module": "module_3"},
        headers=sandbox_admin_headers,
    )
    second = client.post(
        "/api/admin/sandbox/module",
        json={"module": "module_3"},
        headers=sandbox_admin_headers,
    )
    assert first.status_code == second.status_code == 201
    first_body, second_body = first.json(), second.json()
    assert first_body["session_id"] != second_body["session_id"]
    assert first_body["messages"] == second_body["messages"] == []
    assert second_body["next_module"] == "module_3"
    assert second_body["title"] == "沙盒 · MODULE III"

    # Reloading the same row must retain the module pointer for the admin
    # header instead of clearing the badge after a refresh/history switch.
    reloaded = client.get(
        f"/api/conversations/{second_body['session_id']}",
        headers=sandbox_admin_headers,
    )
    assert reloaded.status_code == 200
    assert reloaded.json()["next_module"] == "module_3"

    live = asyncio.run(store.get(second_body["session_id"]))
    assert live is not None
    assert live.module == "module_3"
    assert live.messages == []
    assert live.memory == {
        "sandbox_mode": "true",
        "sandbox_start_module": "module_3",
    }

    # No request-level module override is needed: the fresh session itself is
    # pinned at module 3, which proves the sandbox survives the UI request.
    turn = client.post(
        "/api/chat",
        json={"message": "从这个模块开始", "session_id": second_body["session_id"]},
        headers=sandbox_admin_headers,
    )
    assert turn.status_code == 200, turn.text
    assert turn.json()["reply_module"] == "module_3"
    assert turn.json()["routed_by"] == "sticky"

    async def read_state() -> tuple[ConversationRuntimeState, str | None]:
        async with db_sessionmaker() as db:
            conversation = (
                await db.execute(
                    select(Conversation).where(
                        Conversation.session_id == second_body["session_id"]
                    )
                )
            ).scalar_one()
            runtime = await db.get(ConversationRuntimeState, conversation.id)
            profile_module = (
                await db.execute(
                    select(UserProfile.current_module)
                    .join(UserAccount, UserAccount.profile_uuid == UserProfile.uuid)
                    .where(UserAccount.username == "sandboxadmin")
                )
            ).scalar_one()
            assert runtime is not None
            return runtime, profile_module

    runtime, profile_module = asyncio.run(read_state())
    assert runtime.memory["sandbox_mode"] == "true"
    # Test turns must never advance the administrator's real BA programme.
    assert profile_module == "开场"


def test_invalid_module_is_rejected(
    client: TestClient, sandbox_admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/admin/sandbox/module",
        json={"module": "module_9"},
        headers=sandbox_admin_headers,
    )
    assert response.status_code == 422
