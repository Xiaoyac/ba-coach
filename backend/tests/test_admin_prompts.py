"""Admin prompt editor: permissions, persistence, reset, and graph wiring."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AccountHandle, AccountSettings, UserAccount
from app.providers.base import Completion, as_text


@pytest.fixture
def admin_headers(register, db_sessionmaker) -> dict[str, str]:
    headers = register(username="promptadmin", nickname="Prompt Admin")

    async def promote() -> None:
        async with db_sessionmaker() as db:
            settings = (
                await db.execute(
                    select(AccountSettings)
                    .join(UserAccount, UserAccount.id == AccountSettings.account_id)
                    .join(AccountHandle, AccountHandle.account_id == UserAccount.id)
                    .where(AccountHandle.normalized_base == "prompt admin")
                )
            ).scalar_one()
            settings.role = "admin"
            await db.commit()

    asyncio.run(promote())
    return headers


def test_regular_account_cannot_read_or_write_prompts(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.get("/api/admin/prompts", headers=auth_headers).status_code == 403
    assert (
        client.put(
            "/api/admin/prompts/global",
            json={"content": "not allowed"},
            headers=auth_headers,
        ).status_code
        == 403
    )
    assert (
        client.delete("/api/admin/prompts/global", headers=auth_headers).status_code
        == 403
    )


def test_admin_reads_all_source_defaults(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.get("/api/admin/prompts", headers=admin_headers)
    assert response.status_code == 200
    prompts = response.json()["prompts"]
    assert [item["key"] for item in prompts] == [
        "global",
        "module_1",
        "module_2",
        "module_3",
        "module_4",
        "router_agent",
        "knowledge_mediator",
    ]
    assert all(item["content"] for item in prompts)
    assert all(item["is_overridden"] is False for item in prompts)


def test_admin_can_save_and_restore_an_override(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    saved = client.put(
        "/api/admin/prompts/module_2",
        json={"content": "  CUSTOM MODULE TWO  "},
        headers=admin_headers,
    )
    assert saved.status_code == 200
    assert saved.json()["content"] == "CUSTOM MODULE TWO"
    assert saved.json()["is_overridden"] is True
    assert saved.json()["updated_by"].startswith("Prompt Admin#")

    listed = client.get("/api/admin/prompts", headers=admin_headers).json()["prompts"]
    module_two = next(item for item in listed if item["key"] == "module_2")
    assert module_two["content"] == "CUSTOM MODULE TWO"

    reset = client.delete(
        "/api/admin/prompts/module_2", headers=admin_headers
    )
    assert reset.status_code == 200
    assert reset.json()["is_overridden"] is False
    assert reset.json()["content"] != "CUSTOM MODULE TWO"


def test_blank_override_is_rejected(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.put(
        "/api/admin/prompts/global",
        json={"content": "   \n  "},
        headers=admin_headers,
    )
    assert response.status_code == 400


def test_saved_override_is_used_by_the_next_model_turn(
    client: TestClient,
    admin_headers: dict[str, str],
    auth_headers: dict[str, str],
    provider,
) -> None:
    client.put(
        "/api/admin/prompts/global",
        json={"content": "GLOBAL OVERRIDE SENTINEL"},
        headers=admin_headers,
    )
    client.put(
        "/api/admin/prompts/module_1",
        json={"content": "MODULE ONE OVERRIDE SENTINEL"},
        headers=admin_headers,
    )

    response = client.post(
        "/api/chat",
        json={"message": "hello", "module": "module_1"},
        # A regular user's turn must use the system-wide override saved by a
        # different (administrator) account. Prompt rows are never scoped to
        # the account that edited them.
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    system = as_text(provider.systems[-1])
    assert "GLOBAL OVERRIDE SENTINEL" in system
    assert "MODULE ONE OVERRIDE SENTINEL" in system
    assert system.rfind("MODULE ONE OVERRIDE SENTINEL") < system.rfind("GLOBAL OVERRIDE SENTINEL")


def test_saved_web_override_changes_the_agent_reply(
    client: TestClient,
    admin_headers: dict[str, str],
    auth_headers: dict[str, str],
    provider,
    monkeypatch,
) -> None:
    """Exercise the same PUT used by the web editor through to the reply.

    The provider is deterministic here so the assertion is not probabilistic:
    it returns the sentinel reply only when the saved prompt actually reaches
    the model's system input.  A UI that merely says "saved" while the graph
    keeps using source defaults cannot pass this test.
    """
    sentinel = "WEB PROMPT BEHAVIOUR SENTINEL"
    expected = "网页提示词已经实际改变 Agent 回复"
    saved = client.put(
        "/api/admin/prompts/module_1",
        json={"content": sentinel},
        headers=admin_headers,
    )
    assert saved.status_code == 200

    async def prompt_aware_complete(*, system, messages):
        provider._record(system, messages)
        return Completion(
            text=expected if sentinel in as_text(system) else "仍在使用旧提示词",
            model="prompt-aware-test",
            usage={},
        )

    monkeypatch.setattr(provider, "complete", prompt_aware_complete)
    response = client.post(
        "/api/chat",
        json={"message": "验证网页提示词"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["reply"] == expected


def test_saved_router_override_is_used_by_the_next_routing_call(
    client: TestClient,
    admin_headers: dict[str, str],
    auth_headers: dict[str, str],
    provider,
) -> None:
    client.put(
        "/api/admin/prompts/router_agent",
        json={"content": "ROUTER OVERRIDE SENTINEL"},
        headers=admin_headers,
    )

    response = client.post(
        "/api/chat",
        json={"message": "hello", "module": "module_1"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert any("ROUTER OVERRIDE SENTINEL" in system for system in provider.route_systems)
    assert any("服务器强制的跨轮判定契约" in system for system in provider.route_systems)
