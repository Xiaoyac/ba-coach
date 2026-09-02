"""Shared knowledge imports, module routing, retrieval, and graph wiring."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.knowledge_store import chunk_markdown, import_knowledge_source
from app.models import AccountHandle, AccountSettings, UserAccount
from app.providers.base import as_text
from app.retrieval import DatabaseKnowledgeBase, expand_knowledge_query


@pytest.fixture
def knowledge_admin_headers(register, db_sessionmaker) -> dict[str, str]:
    headers = register(username="knowledgeadmin", nickname="Knowledge Admin")

    async def promote() -> None:
        async with db_sessionmaker() as db:
            settings = (
                await db.execute(
                    select(AccountSettings)
                    .join(UserAccount, UserAccount.id == AccountSettings.account_id)
                    .join(AccountHandle, AccountHandle.account_id == UserAccount.id)
                    .where(AccountHandle.normalized_base == "knowledge admin")
                )
            ).scalar_one()
            settings.role = "admin"
            await db.commit()

    asyncio.run(promote())
    return headers


def test_markdown_chunker_accepts_normal_and_coze_escaped_headings() -> None:
    chunks = chunk_markdown(
        "# 第一章\n\n行为激活内容。\n\n\\## 第二节\n\n活动监测内容。",
        max_chars=50,
    )
    assert [chunk.heading for chunk in chunks] == ["第一章", "第二节"]
    assert chunks[0].content.startswith("# 第一章")
    assert "活动监测" in chunks[1].content


def test_knowledge_api_is_admin_only_and_idempotent(
    client: TestClient,
    auth_headers: dict[str, str],
    knowledge_admin_headers: dict[str, str],
) -> None:
    payload = {
        "name": "BA-test.md",
        "category": "BA",
        "markdown": "# 活动监测\n\n记录活动与情绪之间的联系。",
    }
    assert client.get("/api/admin/knowledge", headers=auth_headers).status_code == 403
    assert (
        client.post("/api/admin/knowledge", json=payload, headers=auth_headers).status_code
        == 403
    )

    imported = client.post(
        "/api/admin/knowledge", json=payload, headers=knowledge_admin_headers
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["modules"] == [
        "module_1",
        "module_2",
        "module_3",
        "module_4",
    ]
    assert imported.json()["chunk_count"] == 1
    assert imported.json()["unchanged"] is False

    repeated = client.post(
        "/api/admin/knowledge", json=payload, headers=knowledge_admin_headers
    )
    assert repeated.status_code == 201
    assert repeated.json()["unchanged"] is True
    listed = client.get(
        "/api/admin/knowledge", headers=knowledge_admin_headers
    ).json()["sources"]
    assert len(listed) == 1
    assert listed[0]["name"] == "BA-test.md"


@pytest.mark.asyncio
async def test_category_routing_matches_the_requested_modules(db_sessionmaker) -> None:
    documents = {
        "BA": "bauniquekey applies everywhere",
        "PA": "pauniquekey only applies to module two",
        "BCT": "bctuniquekey only applies to module three",
        "MI": "miuniquekey only applies to modules two and three",
    }
    async with db_sessionmaker() as db:
        for category, content in documents.items():
            await import_knowledge_source(
                db,
                name=f"{category}.md",
                category=category,
                markdown=content,
                updated_by="test",
            )
        await db.commit()

    knowledge = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    for module in ("module_1", "module_2", "module_3", "module_4"):
        assert await knowledge.search(module=module, query="bauniquekey")

    assert not await knowledge.search(module="module_1", query="miuniquekey")
    assert await knowledge.search(module="module_2", query="miuniquekey")
    assert await knowledge.search(module="module_3", query="miuniquekey")
    assert not await knowledge.search(module="module_4", query="miuniquekey")

    assert not await knowledge.search(module="module_1", query="pauniquekey")
    assert await knowledge.search(module="module_2", query="pauniquekey")
    assert not await knowledge.search(module="module_4", query="pauniquekey")

    assert not await knowledge.search(module="module_2", query="bctuniquekey")
    assert await knowledge.search(module="module_3", query="bctuniquekey")
    assert not await knowledge.search(module="module_4", query="bctuniquekey")


@pytest.mark.asyncio
async def test_chinese_activity_query_reaches_english_compendium_row(
    db_sessionmaker,
) -> None:
    async with db_sessionmaker() as db:
        await import_knowledge_source(
            db,
            name="2024-adult-compendium身体活动分类.xlsx",
            category="PA",
            markdown="# Walking\n\nWalking, general, 3.8 METs.",
            updated_by="test",
        )
        await db.commit()

    assert "walking" in expand_knowledge_query("我想把晚饭后散步作为目标")
    knowledge = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    chunks = await knowledge.search(
        module="module_2", query="我想把晚饭后散步作为目标"
    )
    assert chunks
    assert "Walking, general" in chunks[0].text
    assert not await knowledge.search(
        module="module_1", query="我想把晚饭后散步作为目标"
    )


@pytest.mark.asyncio
async def test_retrieval_reserves_room_for_each_matching_module_category(
    db_sessionmaker,
) -> None:
    async with db_sessionmaker() as db:
        for category in ("BA", "PA", "MI"):
            await import_knowledge_source(
                db,
                name=f"{category}-balanced.md",
                category=category,
                markdown=f"# {category}\n\n共同主题 uniquebalance {category}",
                updated_by="test",
            )
        await db.commit()

    knowledge = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    chunks = await knowledge.search(
        module="module_2", query="共同主题 uniquebalance", top_k=3
    )
    assert len(chunks) == 3
    assert {chunk.source.split("-")[0] for chunk in chunks} == {"BA", "PA", "MI"}


@pytest.mark.asyncio
async def test_duplicate_legacy_ba_aliases_are_kept_but_never_indexed(
    db_sessionmaker,
) -> None:
    async with db_sessionmaker() as db:
        await import_knowledge_source(
            db,
            name="BA-chapters-1-6.md",
            category="BA",
            markdown="legacyduplicateuniquekey",
            updated_by="test",
        )
        await db.commit()

    knowledge = DatabaseKnowledgeBase(lambda: db_sessionmaker)
    assert not await knowledge.search(
        module="module_1", query="legacyduplicateuniquekey"
    )


def test_imported_knowledge_reaches_the_next_agent_turn(
    client: TestClient,
    knowledge_admin_headers: dict[str, str],
    auth_headers: dict[str, str],
    provider,
) -> None:
    sentinel = "ACTIVITY-MONITORING-KNOWLEDGE-SENTINEL"
    response = client.post(
        "/api/admin/knowledge",
        json={
            "name": "graph-wire.md",
            "category": "BA",
            "markdown": f"# Monitoring\n\n{sentinel} activity tracking guidance",
        },
        headers=knowledge_admin_headers,
    )
    assert response.status_code == 201, response.text

    turn = client.post(
        "/api/chat",
        json={"message": sentinel, "module": "module_1"},
        headers=auth_headers,
    )
    assert turn.status_code == 200, turn.text
    assert sentinel in as_text(provider.systems[-1])
