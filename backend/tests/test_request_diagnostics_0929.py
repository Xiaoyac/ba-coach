"""Request diagnostics expose exact owned turns, never nearby traces or prompts."""
import asyncio

import pytest

from test_admin_sandbox import sandbox_admin_headers

from sqlalchemy import event, select

from app.models import AccountSettings, UserAccount, AIExecutionEvent, Conversation, ConversationMessage, ConversationShare
from app.request_diagnostics import requests_for_owned_messages



@pytest.fixture
def auth_headers(sandbox_admin_headers):
    return sandbox_admin_headers


REQUEST_FIELDS = {"stage", "request_id", "provider", "model", "recorded_at", "duration_ms", "error_code"}


def run(awaitable):
    return asyncio.run(awaitable)


def seed(client, headers, maker, *, turns=2):
    response = client.post("/api/conversations", headers=headers)
    assert response.status_code == 201, response.text
    sid = response.json()["session_id"]

    async def arrange():
        async with maker() as db:
            conversation = (await db.execute(select(Conversation).where(Conversation.session_id == sid))).scalar_one()
            ids = []
            for i in range(turns):
                user = ConversationMessage(conversation_id=conversation.id, position=i * 2,
                                           role="user", content="好的")
                reply = ConversationMessage(conversation_id=conversation.id, position=i * 2 + 1,
                                            role="assistant", content="重复正文", model_name="qwen-test")
                db.add_all([user, reply])
                await db.flush()
                ids.append((user.id, reply.id))
            await db.commit()
            return {"sid": sid, "cid": conversation.id, "owner": conversation.subject_id, "ids": ids}
    return run(arrange())


def model_event(chat, stage, request_id, *, assistant=None, metadata=None, **overrides):
    values = dict(conversation_id=chat["cid"], subject_id=chat["owner"], session_id=chat["sid"],
                  stage=stage, provider="deepseek", model_name="qwen-test",
                  provider_request_id=request_id, assistant_message_id=assistant, event_metadata=metadata)
    return AIExecutionEvent(**{**values, **overrides})


def fetch(client, headers, message_id):
    return client.get(f"/api/conversations/messages/{message_id}/requests", headers=headers)


def test_request_endpoint_requires_owner_and_assistant(client, auth_headers, register, db_sessionmaker):
    chat = seed(client, auth_headers, db_sessionmaker, turns=1)
    user_id, reply_id = chat["ids"][0]
    other = register(username="requestotherowner")
    assert fetch(client, {}, reply_id).status_code == 401
    assert fetch(client, other, reply_id).status_code == 403
    async def promote_other():
        async with db_sessionmaker() as db:
            account = (await db.execute(select(AccountSettings).join(UserAccount).where(
                UserAccount.username == "requestotherowner"))).scalar_one()
            account.role = "admin"
            await db.commit()
    run(promote_other())
    assert fetch(client, other, reply_id).status_code == 404
    assert fetch(client, auth_headers, user_id).status_code == 404
    assert fetch(client, auth_headers, 999999).status_code == 404
    response = fetch(client, auth_headers, reply_id)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    data = response.json()
    assert data["user_sent_at"] and data["assistant_created_at"]
    assert len(data["requests"]) == 1
    item = data["requests"][0]
    assert set(item) == REQUEST_FIELDS
    assert {k: item[k] for k in ("stage", "request_id", "provider", "model")} == {
        "stage": "main_generation", "request_id": None, "provider": None, "model": "qwen-test"}
    assert item["recorded_at"] == data["assistant_created_at"]


def test_exact_ids_bind_same_text_turns_without_time_guessing_or_metadata_leaks(client, auth_headers, db_sessionmaker):
    chat = seed(client, auth_headers, db_sessionmaker)
    foreign_chat = seed(client, auth_headers, db_sessionmaker, turns=1)
    first_user, first_reply = chat["ids"][0]
    user_id, reply_id = chat["ids"][1]

    async def arrange():
        async with db_sessionmaker() as db:
            db.add_all([
                model_event(chat, "main_generation", "main-current", assistant=reply_id,
                            metadata={"main_input": "SECRET_PROMPT", "memory": "SECRET_MEMORY"}, duration_ms=123),
                model_event(chat, "module_router", "router-current", metadata={"user_message_id": user_id}),
                model_event(chat, "knowledge_mediator", "mediator-current", metadata={"user_message_id": user_id}),
                model_event(chat, "clinical_extraction", "extraction-direct", assistant=reply_id),
                model_event(chat, "main_generation", "other-turn", assistant=first_reply),
                model_event(chat, "module_router", "other-user-turn", metadata={"user_message_id": first_user}),
                model_event(chat, "clinical_extraction", "NO_NEAREST_TIMESTAMP_GUESS", metadata={}),
                model_event(chat, "module_router", "CONFLICTING_BINDINGS", assistant=first_reply,
                            metadata={"user_message_id": user_id}),
                model_event(chat, "main_generation", "WRONG_OWNER", assistant=reply_id, subject_id="not-owner"),
                model_event(chat, "main_generation", "WRONG_SESSION", assistant=reply_id, session_id="not-this-chat"),
                model_event(foreign_chat, "main_generation", "WRONG_CONVERSATION", assistant=reply_id),
                model_event(chat, "module_router", "STRING_ID_NOT_TRUSTED", metadata={"user_message_id": str(user_id)}),
            ])
            await db.commit()
    run(arrange())
    response = fetch(client, auth_headers, reply_id)
    assert response.status_code == 200, response.text
    requests = response.json()["requests"]
    assert {item["request_id"] for item in requests} == {
        "main-current", "router-current", "mediator-current", "extraction-direct",
    }
    assert all(set(item) == REQUEST_FIELDS for item in requests)
    main = next(item for item in requests if item["request_id"] == "main-current")
    assert main["duration_ms"] == 123 and main["recorded_at"]
    assert "SECRET" not in response.text and "user_message_id" not in response.text
    assert chat["sid"] not in response.text


def test_original_and_recovery_ids_are_distinct_and_legacy_column_is_retained(client, auth_headers, db_sessionmaker):
    chat = seed(client, auth_headers, db_sessionmaker)
    user_id, reply_id = chat["ids"][0]
    _, legacy_reply = chat["ids"][1]

    async def arrange():
        async with db_sessionmaker() as db:
            db.add_all([
                model_event(chat, "main_generation", "main-retry", assistant=reply_id, metadata={
                    "reply_recovery": {"original_request_id": "main-first", "request_id": "main-retry", "duration_ms": 900}}),
                model_event(chat, "module_router", "router-first", metadata={"user_message_id": user_id,
                    "json_recovery": {"original_request_id": "router-first", "request_id": "router-retry", "duration_ms": 30}}),
            ])
            legacy = await db.get(ConversationMessage, legacy_reply)
            legacy.provider_request_id = "legacy-main-column"
            await db.commit()
    run(arrange())
    items = fetch(client, auth_headers, reply_id).json()["requests"]
    assert {(item["stage"], item["request_id"]) for item in items} == {
        ("main_generation", "main-first"), ("main_generation_recovery", "main-retry"),
        ("module_router_original", "router-first"), ("module_router_recovery", "router-retry"),
    }
    by_stage = {item["stage"]: item for item in items}
    assert all(item["recorded_at"] for item in items)
    assert by_stage["main_generation"]["duration_ms"] is None
    assert by_stage["main_generation_recovery"]["duration_ms"] == 900
    assert by_stage["module_router_original"]["duration_ms"] is None
    assert by_stage["module_router_recovery"]["duration_ms"] == 30
    legacy = fetch(client, auth_headers, legacy_reply).json()["requests"]
    assert len(legacy) == 1
    assert legacy[0]["request_id"] == "legacy-main-column" and legacy[0]["model"] == "qwen-test"


def test_duplicate_legacy_slots_never_attach_pre_reply_events(client, auth_headers, db_sessionmaker):
    chat = seed(client, auth_headers, db_sessionmaker)
    first_user, first_reply = chat["ids"][0]
    second_user, second_reply = chat["ids"][1]

    async def arrange():
        async with db_sessionmaker() as db:
            db.add_all([
                ConversationMessage(conversation_id=chat["cid"], position=0, role="user", content="duplicate slot"),
                ConversationMessage(conversation_id=chat["cid"], position=3, role="assistant", content="duplicate reply"),
                model_event(chat, "module_router", "AMBIGUOUS_USER", metadata={"user_message_id": first_user}),
                model_event(chat, "module_router", "AMBIGUOUS_REPLY", metadata={"user_message_id": second_user}),
                model_event(chat, "main_generation", "EXPLICIT_REPLY", assistant=first_reply),
            ])
            await db.commit()
    run(arrange())
    first = fetch(client, auth_headers, first_reply).json()["requests"]
    assert [item["request_id"] for item in first] == ["EXPLICIT_REPLY"]
    second = fetch(client, auth_headers, second_reply).json()["requests"]
    assert [item["request_id"] for item in second] == [None]


def test_public_shares_omit_request_details_in_new_and_old_snapshots(client, auth_headers, db_sessionmaker):
    chat = seed(client, auth_headers, db_sessionmaker, turns=1)
    async def make_member():
        async with db_sessionmaker() as db:
            account = await db.scalar(select(UserAccount).where(UserAccount.profile_uuid == chat['owner']))
            settings = await db.get(AccountSettings, account.id)
            settings.role = 'user'
            await db.commit()
    run(make_member())
    response = client.post(f"/api/conversations/{chat['sid']}/shares", headers=auth_headers)
    assert response.status_code == 201
    shared = response.json()

    async def add_legacy_debug_payload():
        async with db_sessionmaker() as db:
            snapshot = await db.get(ConversationShare, shared["id"])
            data = dict(snapshot.snapshot)
            data["messages"] = [{**message, "model_requests": [{"stage": "main_generation", "request_id": "PRIVATE_ID"}]}
                                for message in data["messages"]]
            snapshot.snapshot = data
            await db.commit()
    for before_legacy in (True, False):
        if not before_legacy:
            run(add_legacy_debug_payload())
        published = client.get(f"/api/shares/{shared['token']}")
        assert published.status_code == 200
        assert all(set(message) == {"id", "role", "content", "created_at", "reply_status"}
                   for message in published.json()["messages"])
        assert "PRIVATE_ID" not in published.text


def test_shared_helper_batches_queries_and_does_not_write(client, auth_headers, db_sessionmaker):
    chat = seed(client, auth_headers, db_sessionmaker, turns=8)

    async def inspect():
        async with db_sessionmaker() as db:
            conversation = await db.get(Conversation, chat["cid"])
            assistants = [message for message in conversation.messages if message.role == "assistant"]
            statements = []

            def observe(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement.lstrip().upper())
            event.listen(db.bind.sync_engine, "before_cursor_execute", observe)
            try:
                result = await requests_for_owned_messages(db, owned_conversation=conversation, messages=assistants)
            finally:
                event.remove(db.bind.sync_engine, "before_cursor_execute", observe)
            assert len(result) == 9  # Eight replies plus the existing opening.
            assert len(statements) == 2
            assert all(statement.startswith("SELECT") for statement in statements)
    run(inspect())


def test_request_timestamp_schema_keeps_database_utc_explicit():
    from datetime import datetime
    from app.request_diagnostics import MessageRequests, ModelRequestInfo
    naive = datetime(2026, 9, 30, 8, 0)
    data = MessageRequests(user_sent_at=naive, assistant_created_at=naive,
        requests=[ModelRequestInfo(stage="main_generation", recorded_at=naive)]).model_dump(mode="json")
    assert data["user_sent_at"].endswith("Z")
    assert data["assistant_created_at"] == data["requests"][0]["recorded_at"]
