"""HTTP tests for /api/conversations — listing, renaming, pinning, deleting.

The `client` fixture points `get_db` at a throwaway in-memory SQLite database
(see conftest), so these create and mutate real rows without touching whatever
`DATABASE_URL` happens to name.

Identity comes from a real registered account rather than an invented header:
`subject_id` is now `user_profile.uuid`, minted server-side at registration.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.opening import OPENING_MESSAGE_TEXT
from app.conversation_store import backfill_opening_messages

@pytest.fixture
def headers(auth_headers: dict[str, str]) -> dict[str, str]:
    """The default account's auth headers, under the name these tests use."""
    return auth_headers


@pytest.fixture
def other_headers(register) -> dict[str, str]:
    """A second, unrelated account — for the ownership-isolation tests."""
    return register(username="someoneelse")


def _start(client: TestClient, message: str, headers: dict[str, str]) -> str:
    """Create a conversation the only way the app does — by taking a turn."""
    response = client.post("/api/chat", json={"message": message}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def _titles(client: TestClient, headers: dict[str, str]) -> list[str]:
    return [c["title"] for c in client.get("/api/conversations", headers=headers).json()]


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_create_conversation_persists_opening_and_seeds_agent_history(
    client: TestClient,
    headers: dict[str, str],
    provider,
) -> None:
    created = client.post("/api/conversations", headers=headers)
    assert created.status_code == 201, created.text
    detail = created.json()
    assert detail["title"] == "新对话"
    assert detail["next_module"] is None
    assert isinstance(detail["messages"][0]["id"], int)
    assert detail["messages"] == [
        {
            "id": detail["messages"][0]["id"],
            "role": "assistant",
            "content": OPENING_MESSAGE_TEXT,
            "reasoning_content": None,
            "model_name": None,
            "routing_reasoning_content": None,
            "router_model_name": None,
            "timing": None,
        }
    ]

    response = client.post(
        "/api/chat",
        json={"message": "我叫小雨", "session_id": detail["session_id"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert [message.content for message in provider.seen[-1]] == [
        OPENING_MESSAGE_TEXT,
        "<user_message>我叫小雨</user_message>",
    ]

    refreshed = client.get(
        f"/api/conversations/{detail['session_id']}", headers=headers
    ).json()
    assert refreshed["next_module"] in {"module_1", "module_2", "module_3", "module_4"}
    assert refreshed["messages"][0] == {
        "id": detail["messages"][0]["id"],
        "role": "assistant",
        "content": OPENING_MESSAGE_TEXT,
        "reasoning_content": None,
        "model_name": None,
        "routing_reasoning_content": None,
        "router_model_name": None,
        "timing": None,
    }
    assert len(refreshed["messages"]) == 3


def test_create_conversation_requires_authentication(client: TestClient) -> None:
    assert client.post("/api/conversations").status_code == 401


def test_legacy_conversation_opening_backfill_is_idempotent(
    client: TestClient,
    headers: dict[str, str],
    db_sessionmaker,
) -> None:
    import asyncio

    session_id = _start(client, "旧版第一句话", headers)

    async def repair_twice() -> tuple[int, int]:
        async with db_sessionmaker() as db:
            first = await backfill_opening_messages(db)
        async with db_sessionmaker() as db:
            second = await backfill_opening_messages(db)
        return first, second

    assert asyncio.get_event_loop().run_until_complete(repair_twice()) == (1, 0)
    detail = client.get(f"/api/conversations/{session_id}", headers=headers).json()
    assert detail["messages"][0]["content"] == OPENING_MESSAGE_TEXT


def test_list_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/conversations").status_code == 401


def test_a_forged_token_is_rejected(client: TestClient) -> None:
    """The whole point of the rewrite: identity can no longer be asserted."""
    response = client.get(
        "/api/conversations", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_list_is_scoped_to_the_account(
    client: TestClient, headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    _start(client, "mine", headers)
    _start(client, "theirs", other_headers)

    assert _titles(client, headers) == ["mine"]
    assert _titles(client, other_headers) == ["theirs"]


def test_new_conversations_are_unpinned(
    client: TestClient, headers: dict[str, str]
) -> None:
    _start(client, "hello", headers)
    rows = client.get("/api/conversations", headers=headers).json()
    assert rows[0]["pinned"] is False


def test_revision_changes_when_a_new_turn_is_saved(
    client: TestClient, headers: dict[str, str]
) -> None:
    session_id = _start(client, "first", headers)
    first = client.get(
        f"/api/conversations/{session_id}/revision", headers=headers
    ).json()["revision"]

    response = client.post(
        "/api/chat",
        json={"message": "second", "session_id": session_id},
        headers=headers,
    )
    assert response.status_code == 200
    second = client.get(
        f"/api/conversations/{session_id}/revision", headers=headers
    ).json()["revision"]

    assert second > first


def test_revision_does_not_reveal_another_accounts_conversation(
    client: TestClient, headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    session_id = _start(client, "mine", headers)
    response = client.get(
        f"/api/conversations/{session_id}/revision", headers=other_headers
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Renaming
# ---------------------------------------------------------------------------


def test_rename_changes_the_title(client: TestClient, headers: dict[str, str]) -> None:
    session_id = _start(client, "原来的标题", headers)

    response = client.patch(
        f"/api/conversations/{session_id}", json={"title": "睡眠问题"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["title"] == "睡眠问题"
    assert _titles(client, headers) == ["睡眠问题"]


def test_rename_does_not_bump_updated_at(
    client: TestClient, headers: dict[str, str]
) -> None:
    """Renaming is not talking to a conversation — it must not reorder the list."""
    session_id = _start(client, "first", headers)
    before = client.get("/api/conversations", headers=headers).json()[0]["updated_at"]

    client.patch(
        f"/api/conversations/{session_id}", json={"title": "renamed"}, headers=headers
    )

    after = client.get("/api/conversations", headers=headers).json()[0]["updated_at"]
    assert after == before


def test_rename_strips_surrounding_whitespace(
    client: TestClient, headers: dict[str, str]
) -> None:
    session_id = _start(client, "x", headers)
    body = client.patch(
        f"/api/conversations/{session_id}", json={"title": "  有空格  "}, headers=headers
    ).json()
    assert body["title"] == "有空格"


def test_whitespace_only_title_is_rejected(
    client: TestClient, headers: dict[str, str]
) -> None:
    session_id = _start(client, "x", headers)
    response = client.patch(
        f"/api/conversations/{session_id}", json={"title": "   "}, headers=headers
    )
    assert response.status_code == 400


def test_overlong_title_is_rejected(
    client: TestClient, headers: dict[str, str]
) -> None:
    session_id = _start(client, "x", headers)
    response = client.patch(
        f"/api/conversations/{session_id}", json={"title": "x" * 81}, headers=headers
    )
    assert response.status_code == 422


def test_empty_patch_is_rejected(client: TestClient, headers: dict[str, str]) -> None:
    """An all-null body is a caller bug; 400 rather than a silent no-op."""
    session_id = _start(client, "x", headers)
    response = client.patch(
        f"/api/conversations/{session_id}", json={}, headers=headers
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------


def test_pinned_sorts_above_more_recent_conversations(
    client: TestClient, headers: dict[str, str]
) -> None:
    older = _start(client, "older", headers)
    _start(client, "newer", headers)

    # Without pinning, the newer one leads.
    assert _titles(client, headers)[0] == "newer"

    client.patch(f"/api/conversations/{older}", json={"pinned": True}, headers=headers)

    rows = client.get("/api/conversations", headers=headers).json()
    assert rows[0]["title"] == "older"
    assert rows[0]["pinned"] is True


def test_unpinning_restores_recency_order(
    client: TestClient, headers: dict[str, str]
) -> None:
    older = _start(client, "older", headers)
    _start(client, "newer", headers)
    client.patch(f"/api/conversations/{older}", json={"pinned": True}, headers=headers)
    client.patch(f"/api/conversations/{older}", json={"pinned": False}, headers=headers)

    assert _titles(client, headers) == ["newer", "older"]


def test_rename_and_pin_in_one_request(
    client: TestClient, headers: dict[str, str]
) -> None:
    session_id = _start(client, "x", headers)
    body = client.patch(
        f"/api/conversations/{session_id}",
        json={"title": "两个都改", "pinned": True},
        headers=headers,
    ).json()
    assert body["title"] == "两个都改"
    assert body["pinned"] is True


def test_patching_one_field_leaves_the_other_alone(
    client: TestClient, headers: dict[str, str]
) -> None:
    session_id = _start(client, "x", headers)
    client.patch(
        f"/api/conversations/{session_id}",
        json={"title": "命名", "pinned": True},
        headers=headers,
    )
    # Rename again without mentioning `pinned` — it must survive.
    body = client.patch(
        f"/api/conversations/{session_id}", json={"title": "改名"}, headers=headers
    ).json()
    assert body["title"] == "改名"
    assert body["pinned"] is True


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_cannot_patch_another_accounts_conversation(
    client: TestClient, headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    """404, not 403 — a probe must not learn that the id exists."""
    session_id = _start(client, "mine", headers)
    response = client.patch(
        f"/api/conversations/{session_id}",
        json={"title": "hijacked"},
        headers=other_headers,
    )
    assert response.status_code == 404
    assert _titles(client, headers) == ["mine"]


def test_cannot_read_another_accounts_conversation(
    client: TestClient, headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    session_id = _start(client, "mine", headers)
    response = client.get(f"/api/conversations/{session_id}", headers=other_headers)
    assert response.status_code == 404


def test_patching_an_unknown_conversation_is_404(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/conversations/does-not-exist", json={"title": "x"}, headers=headers
    )
    assert response.status_code == 404


def test_delete_removes_it_from_the_list(
    client: TestClient, headers: dict[str, str]
) -> None:
    session_id = _start(client, "throwaway", headers)
    assert (
        client.delete(f"/api/conversations/{session_id}", headers=headers).status_code
        == 204
    )
    assert client.get("/api/conversations", headers=headers).json() == []


# ---------------------------------------------------------------------------
# Surviving a lost session — the "two conversation records" bug
# ---------------------------------------------------------------------------


def test_overlapping_turns_reserve_pairs_without_losing_either_message(
    db_sessionmaker,
) -> None:
    """Device B may send while device A's model is still generating.

    Both user rows must be durable immediately, and the eventual replies must
    return to their reserved slots rather than overwriting or interleaving the
    two logical turns.
    """
    import asyncio

    from sqlalchemy import select

    from app.conversation_store import finish_turn, start_turn
    from app.models import ConversationMessage

    async def scenario():
        async with db_sessionmaker() as first_db:
            first_id = await start_turn(
                first_db,
                subject_id="same-account",
                session_id="shared-session",
                user_text="设备 A",
            )

        # The user's text is visible before any assistant reply exists.
        async with db_sessionmaker() as read_db:
            visible = (
                await read_db.execute(
                    select(ConversationMessage).order_by(ConversationMessage.position)
                )
            ).scalars().all()
            assert [(m.role, m.content) for m in visible] == [("user", "设备 A")]

        async with db_sessionmaker() as second_db:
            second_id = await start_turn(
                second_db,
                subject_id="same-account",
                session_id="shared-session",
                user_text="设备 B",
            )

        async with db_sessionmaker() as first_reply_db:
            await finish_turn(
                first_reply_db,
                session_id="shared-session",
                user_message_id=first_id,
                reply_text="回复 A",
            )
        async with db_sessionmaker() as second_reply_db:
            await finish_turn(
                second_reply_db,
                session_id="shared-session",
                user_message_id=second_id,
                reply_text="回复 B",
            )

        async with db_sessionmaker() as final_db:
            messages = (
                await final_db.execute(
                    select(ConversationMessage).order_by(
                        ConversationMessage.position, ConversationMessage.id
                    )
                )
            ).scalars().all()
            return [(m.position, m.role, m.content) for m in messages]

    assert asyncio.run(scenario()) == [
        (0, "user", "设备 A"),
        (1, "assistant", "回复 A"),
        (2, "user", "设备 B"),
        (3, "assistant", "回复 B"),
    ]


def test_latency_telemetry_and_background_route_are_durable(
    db_sessionmaker,
) -> None:
    import asyncio

    from sqlalchemy import select

    from app.conversation_store import (
        complete_background_routing,
        finish_turn,
        start_turn,
    )
    from app.models import AIExecutionEvent, Conversation, ConversationMessage, ConversationRuntimeState

    async def scenario() -> None:
        async with db_sessionmaker() as db:
            user_id = await start_turn(
                db,
                subject_id="subject",
                session_id="telemetry-session",
                user_text="hello",
            )
        async with db_sessionmaker() as db:
            assistant_id = await finish_turn(
                db,
                session_id="telemetry-session",
                user_message_id=user_id,
                reply_text="reply",
                telemetry={
                    "risk_gate_duration_ms": 12,
                    "time_to_first_reasoning_token_ms": 34,
                    "time_to_first_content_token_ms": 56,
                    "time_to_first_visible_content_ms": 123,
                    "first_visible_measurement": "server_sse_release",
                    "main_generation_duration_ms": 789,
                    "provider_request_id": "req-1",
                    "finish_reason": "stop",
                    "prompt_version": "0123456789abcdef",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "reasoning_tokens": 7,
                    },
                },
            )
        assert assistant_id is not None

        async with db_sessionmaker() as db:
            conversation = (
                await db.execute(
                    select(Conversation).where(
                        Conversation.session_id == "telemetry-session"
                    )
                )
            ).scalar_one()
            before_revision = conversation.revision

        async with db_sessionmaker() as db:
            assert await complete_background_routing(
                db,
                subject_id="subject",
                session_id="telemetry-session",
                assistant_message_id=assistant_id,
                module="module_2",
                memory={"turn_count": "1"},
                routing_reasoning_content="route thought",
                router_model_name="deepseek-router",
                router_duration_ms=321,
            )

        async with db_sessionmaker() as db:
            message = await db.get(ConversationMessage, assistant_id)
            conversation = (
                await db.execute(
                    select(Conversation).where(
                        Conversation.session_id == "telemetry-session"
                    )
                )
            ).scalar_one()
            runtime = await db.get(ConversationRuntimeState, conversation.id)
            assert message is not None
            assert message.risk_gate_duration_ms == 12
            assert message.time_to_first_content_token_ms == 56
            assert message.main_generation_duration_ms == 789
            assert message.router_duration_ms == 321
            assert message.reasoning_tokens == 7
            assert message.provider_request_id == "req-1"
            assert message.routing_reasoning_content == "route thought"
            event = (await db.execute(select(AIExecutionEvent).where(
                AIExecutionEvent.assistant_message_id == assistant_id,
                AIExecutionEvent.stage == "main_generation",
            ))).scalar_one()
            assert event.event_metadata["time_to_first_visible_content_ms"] == 123
            assert event.event_metadata["first_visible_measurement"] == "server_sse_release"
            assert conversation.revision == before_revision + 1
            assert runtime is not None and runtime.module == "module_2"

    asyncio.run(scenario())


def test_one_session_uses_one_turn_lock(store) -> None:
    """Both HTTP requests must queue behind the same conversation lock."""
    import asyncio

    async def scenario():
        first = await store.get_turn_lock("shared-session")
        second = await store.get_turn_lock("shared-session")
        assert first is second

        release = asyncio.Event()
        first_entered = asyncio.Event()
        order: list[str] = []

        async def device_a():
            async with first:
                order.append("A start")
                first_entered.set()
                await release.wait()
                order.append("A end")

        async def device_b():
            await first_entered.wait()
            async with second:
                order.append("B start")
                order.append("B end")

        task_a = asyncio.create_task(device_a())
        task_b = asyncio.create_task(device_b())
        await first_entered.wait()
        await asyncio.sleep(0)
        assert order == ["A start"]
        release.set()
        await asyncio.gather(task_a, task_b)
        return order

    assert asyncio.run(scenario()) == [
        "A start",
        "A end",
        "B start",
        "B end",
    ]


def test_a_lost_session_resumes_instead_of_forking(
    client: TestClient, headers: dict[str, str], store
) -> None:
    """A restart must not turn one conversation into two.

    The session store is in-process and TTL'd. Before this, a client whose
    session the server had forgotten got a brand-new id on its next message,
    so the turn landed in a *new* conversation while the client still showed
    the old one — two sidebar rows for one chat.
    """
    session_id = _start(client, "第一条消息", headers)
    assert _titles(client, headers) == ["第一条消息"]

    # Exactly what a restart looks like from the client's side: the id it
    # holds is still valid, the server has simply forgotten it.
    import asyncio

    asyncio.get_event_loop().run_until_complete(store.reset(session_id))

    response = client.post(
        "/api/chat",
        json={"message": "重启之后的第二条", "session_id": session_id},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["session_id"] == session_id, "the id must be kept"

    # Still one conversation, now four messages deep.
    assert _titles(client, headers) == ["第一条消息"]
    detail = client.get(f"/api/conversations/{session_id}", headers=headers).json()
    assert [m["content"] for m in detail["messages"]][::2] == [
        "第一条消息",
        "重启之后的第二条",
    ]


def test_a_resumed_session_still_carries_its_history(
    client: TestClient, headers: dict[str, str], store, provider
) -> None:
    """Resuming must rebuild the transcript, not just reuse the id."""
    import asyncio

    session_id = _start(client, "一", headers)
    asyncio.get_event_loop().run_until_complete(store.reset(session_id))

    client.post(
        "/api/chat", json={"message": "二", "session_id": session_id}, headers=headers
    )
    # The model saw the rehydrated history, not a bare first turn.
    assert [m.content for m in provider.seen[-1]] == ["一", "saw 1 messages", "<user_message>二</user_message>"]


def test_a_resumed_session_keeps_the_router_module(
    client: TestClient, headers: dict[str, str], store, provider
) -> None:
    """A process restart must not silently send a later turn back to MODULE I."""
    import asyncio

    provider.route_result = '{"target_module":"2","completed_steps":["core_problem_example","depression_cycle_formulated","ba_education_completed","goal_setting_consent"]}'
    first = client.post("/api/chat", json={"message": "模块一已完成"}, headers=headers)
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    asyncio.get_event_loop().run_until_complete(store.reset(session_id))
    provider.route_result = ""
    second = client.post(
        "/api/chat",
        json={"message": "现在设定目标", "session_id": session_id},
        headers=headers,
    )
    assert second.status_code == 200
    # The reply proves rehydration restored module 2. `next_module` is
    # intentionally null in the immediate response while the post-hoc router
    # is still pending; the durable conversation snapshot receives it later.
    assert second.json()["reply_module"] == "module_2"
    assert second.json()["next_module"] == second.json()["reply_module"]
    assert second.json()["routing_pending"] is False


def test_another_accounts_session_id_is_never_adopted(
    client: TestClient, headers: dict[str, str], other_headers: dict[str, str], store
) -> None:
    """Ownership is what makes resuming safe — a guessed id must still fork."""
    import asyncio

    session_id = _start(client, "我的对话", headers)
    asyncio.get_event_loop().run_until_complete(store.reset(session_id))

    stolen = client.post(
        "/api/chat",
        json={"message": "我想读你的对话", "session_id": session_id},
        headers=other_headers,
    )
    assert stolen.status_code == 200
    assert stolen.json()["session_id"] != session_id

    # And the original is untouched.
    detail = client.get(f"/api/conversations/{session_id}", headers=headers).json()
    assert len(detail["messages"]) == 2


def test_an_unauthenticated_caller_cannot_resume(client: TestClient, store) -> None:
    import asyncio

    session_id = client.post("/api/chat", json={"message": "hi"}).json()["session_id"]
    asyncio.get_event_loop().run_until_complete(store.reset(session_id))

    body = client.post(
        "/api/chat", json={"message": "again", "session_id": session_id}
    ).json()
    assert body["session_id"] != session_id


# ---------------------------------------------------------------------------
# Deletion actually deleting
# ---------------------------------------------------------------------------


def test_deleting_removes_the_messages_too(
    client: TestClient, headers: dict[str, str], db_sessionmaker
) -> None:
    import asyncio

    from sqlalchemy import select

    from app.models import Conversation, ConversationMessage

    session_id = _start(client, "会被删掉", headers)

    async def counts():
        async with db_sessionmaker() as db:
            convs = (await db.execute(select(Conversation))).scalars().all()
            msgs = (await db.execute(select(ConversationMessage))).scalars().all()
            return len(convs), len(msgs)

    assert asyncio.get_event_loop().run_until_complete(counts()) == (1, 2)
    client.delete(f"/api/conversations/{session_id}", headers=headers)
    assert asyncio.get_event_loop().run_until_complete(counts()) == (0, 0)


def test_a_deleted_conversation_cannot_be_resurrected(
    client: TestClient, headers: dict[str, str]
) -> None:
    """The bug this closes: deleting left the live session alive.

    Anything still holding the id — the other device, an unrefreshed tab —
    sent it with its next message, the store still recognised it, and
    `record_turn` wrote the row straight back. The delete looked like it had
    silently failed.
    """
    session_id = _start(client, "删掉我", headers)
    assert client.delete(f"/api/conversations/{session_id}", headers=headers).status_code == 204
    assert _titles(client, headers) == []

    # A client that still holds the id keeps typing.
    body = client.post(
        "/api/chat",
        json={"message": "删掉之后我又说话了", "session_id": session_id},
        headers=headers,
    ).json()

    assert body["session_id"] != session_id, "the deleted id must be dead"
    assert "删掉我" not in _titles(client, headers)


def test_a_deleted_conversation_does_not_resume_from_the_database(
    client: TestClient, headers: dict[str, str], store
) -> None:
    """And the resume path must not bring it back either."""
    import asyncio

    session_id = _start(client, "删掉我", headers)
    client.delete(f"/api/conversations/{session_id}", headers=headers)
    # Even with the store already cold, there is no row left to resume from.
    asyncio.get_event_loop().run_until_complete(store.reset(session_id))

    body = client.post(
        "/api/chat", json={"message": "再来一句", "session_id": session_id}, headers=headers
    ).json()
    assert body["session_id"] != session_id


def test_delete_removes_linked_legacy_goal_and_history_only(client, headers, db_sessionmaker):
    import asyncio
    from sqlalchemy import select
    from app.models import Conversation, PACycle, ClinicalRecordCycleLink
    from app.models_business import ModuleTwoRecord, InteractionStatus
    session_id = _start(client, "delete linked goal", headers)

    async def seed():
        async with db_sessionmaker() as db:
            conv = (await db.execute(select(Conversation).where(Conversation.session_id == session_id))).scalar_one()
            db.add(PACycle(id="delete-cycle",conversation_id=conv.id,subject_id=conv.subject_id,ordinal=999))
            db.add_all([ModuleTwoRecord(id="delete-plan",user_id=conv.subject_id,target_activity_content="delete me"),
                        ModuleTwoRecord(id="unlinked-plan",user_id=conv.subject_id,target_activity_content="keep me")])
            await db.flush()
            db.add(ClinicalRecordCycleLink(cycle_id="delete-cycle",module="module_2",record_id="delete-plan"))
            interaction=(await db.execute(select(InteractionStatus).where(InteractionStatus.user_id==conv.subject_id))).scalar_one_or_none()
            if interaction is None:
                interaction=InteractionStatus(user_id=conv.subject_id)
                db.add(interaction)
            interaction.goal_history=[{"cycle_id":"delete-cycle","activity":"delete me"},{"cycle_id":"other-cycle","activity":"keep me"}]
            await db.commit()
    asyncio.get_event_loop().run_until_complete(seed())
    assert client.delete(f"/api/conversations/{session_id}",headers=headers).status_code==204
    async def check():
        async with db_sessionmaker() as db:
            assert await db.get(ModuleTwoRecord,"delete-plan") is None
            assert await db.get(ModuleTwoRecord,"unlinked-plan") is not None
            history=(await db.execute(select(InteractionStatus.goal_history))).scalar_one()
            assert history==[{"cycle_id":"other-cycle","activity":"keep me"}]
    asyncio.get_event_loop().run_until_complete(check())
