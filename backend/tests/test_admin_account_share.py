"""An administrator puts part of a member's chat, debug details included, behind a share link."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.graph import nodes
from app.models import AIExecutionEvent, Conversation, ConversationMessage, ConversationShare, UserAccount
from test_admin_accounts import admin_headers


@pytest.fixture
def member_chat(client, admin_headers, register, db_sessionmaker):
    """A member with one five-message chat; its second reply has every debug detail saved."""
    headers = register(username="sharedmember", nickname="被分享用户")
    account = next(item for item in client.get(
        "/api/admin/accounts", params={"query": "sharedmember"}, headers=admin_headers).json()["accounts"])
    ids = {}

    async def seed():
        async with db_sessionmaker() as db:
            subject = (await db.execute(select(UserAccount.profile_uuid).where(
                UserAccount.username == "sharedmember"))).scalar_one()
            chat = Conversation(subject_id=subject, session_id="member-chat", title="最近有点累")
            chat.messages = [
                ConversationMessage(position=0, role="user", content="范围之外的第一句"),
                ConversationMessage(position=1, role="assistant", content="范围之外的回复", reasoning_content="范围之外的思考"),
                ConversationMessage(position=2, role="user", content="想去散步"),
                ConversationMessage(position=3, role="assistant", content="先走五分钟。",
                    reasoning_content="回复思考", model_name="reply-model",
                    routing_reasoning_content="路由思考", router_model_name="router-model",
                    provider_request_id="main-request", main_generation_duration_ms=200,
                    time_to_first_reasoning_token_ms=10, time_to_first_content_token_ms=60, router_duration_ms=80),
                ConversationMessage(position=4, role="user", content="没有得到回复的最后一句"),
            ]
            other = Conversation(subject_id="someone-else", session_id="other-chat", title="别人的")
            other.messages = [ConversationMessage(position=0, role="user", content="别人的话")]
            db.add_all([chat, other])
            await db.flush()
            metadata = {"knowledge_references": {
                "available": True, "mediator_guidance": "中介建议", "mediator_reasoning_content": "中介思考",
                "mediator_model": "mediator-model", "mediator_duration_ms": 35,
                "recalled": [{"id": "chunk", "source": "教材", "text": "知识片段"}],
                "provided": [{"id": "chunk", "source": "教材", "text": "知识片段"}],
            }, "private_profile": "UNRELATED_PROFILE"}
            for stage, request_id, data in [
                ("main_generation", "main-request", metadata),
                ("module_router", "router-request", {"user_message_id": chat.messages[2].id}),
            ]:
                db.add(AIExecutionEvent(subject_id=subject, conversation_id=chat.id, session_id=chat.session_id,
                    assistant_message_id=chat.messages[3].id, stage=stage, provider="stub",
                    model_name=stage, provider_request_id=request_id, event_metadata=data))
            ids.update(chat=[message.id for message in chat.messages], other=other.messages[0].id)
            await db.commit()

    client.portal.call(seed)
    return SimpleNamespace(
        headers=headers, account=account, ids=ids, url=f"/api/admin/accounts/{account['id']}/shares",
        # Ends given backwards: the third and fourth messages.
        body={"session_id": "member-chat", "first_message_id": ids["chat"][3], "last_message_id": ids["chat"][2]},
    )


def share_count(client, db_sessionmaker):
    async def count():
        async with db_sessionmaker() as db:
            return len((await db.execute(select(ConversationShare))).scalars().all())
    return client.portal.call(count)


def test_admin_shares_a_picked_run_of_a_members_chat(client, admin_headers, member_chat, db_sessionmaker, provider):
    research_id = member_chat.account["research_id"]
    assert research_id
    created = client.post(member_chat.url, headers=admin_headers, json=member_chat.body)
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "private, no-store"
    made = created.json()
    assert (made["snapshot_version"], made["message_count"]) == (2, 2)
    assert made["title"] == f"{research_id} · 最近有点累（节选）"

    response = client.get(f"/api/shares/{made['token']}")  # no login
    assert response.status_code == 200
    shared = response.json()
    assert shared["title"] == made["title"]
    # Only the picked run, numbered locally to the snapshot.
    assert [(m["id"], m["role"], m["content"]) for m in shared["messages"]] == [
        (1, "user", "想去散步"), (2, "assistant", "先走五分钟。")]
    reply = shared["messages"][1]
    assert reply["reasoning_content"] == "回复思考"
    assert reply["routing_reasoning_content"] == "路由思考"
    assert reply["model_name"] == "reply-model" and reply["router_model_name"] == "router-model"
    assert reply["timing"] == {"reply_thinking_ms": 50, "reply_generation_ms": 200, "router_processing_ms": 80}
    assert reply["knowledge_references"]["mediator_guidance"] == "中介建议"
    assert reply["knowledge_references"]["mediator_reasoning_content"] == "中介思考"
    assert reply["knowledge_references"]["provided"][0]["text"] == "知识片段"
    assert {r["request_id"] for r in reply["request_records"]["requests"]} == {"main-request", "router-request"}
    # Nothing outside the run and nothing that names the member is published.
    for private in ("范围之外", "没有得到回复", "UNRELATED_PROFILE", "sharedmember", "被分享用户", "13800138000"):
        assert private not in response.text
    assert provider.seen == [] and provider.route_calls == []

    # The whole chat can be shared too, even though its last turn was never answered.
    whole = client.post(member_chat.url, headers=admin_headers, json={
        "session_id": "member-chat",
        "first_message_id": member_chat.ids["chat"][0], "last_message_id": member_chat.ids["chat"][4]})
    assert whole.status_code == 201, whole.text
    assert (whole.json()["title"], whole.json()["message_count"]) == (f"{research_id} · 最近有点累", 5)
    assert share_count(client, db_sessionmaker) == 2
    # The link stays a frozen copy: the member's own, private view is untouched.
    assert client.get("/api/conversations/member-chat").status_code == 401


def test_share_route_is_for_admins_and_stays_inside_the_account(client, admin_headers, member_chat, db_sessionmaker):
    def post(account_id, headers=admin_headers, **changes):
        return client.post(f"/api/admin/accounts/{account_id}/shares", headers=headers,
                           json={**member_chat.body, **changes})

    own = member_chat.account["id"]
    # A member cannot publish debug details this way, not even of their own chat.
    assert post(own, headers=member_chat.headers).status_code == 403
    assert post(999999).status_code == 404
    # Another subject's chat is not reachable through this account, nor is its message.
    assert post(own, session_id="other-chat").status_code == 404
    assert post(own, last_message_id=member_chat.ids["other"]).status_code == 404
    assert post(own, include_everything=True).status_code == 422
    assert share_count(client, db_sessionmaker) == 0


@pytest.mark.parametrize("busy", ["turn", "background"])
def test_share_is_refused_while_the_member_is_mid_turn(
    client, admin_headers, member_chat, store, db_sessionmaker, monkeypatch, busy,
):
    lock = client.portal.call(store.get_turn_lock, "member-chat")
    if busy == "turn":
        client.portal.call(lock.acquire)
    else:
        monkeypatch.setitem(nodes._routing_tasks, "member-chat", SimpleNamespace(done=lambda: False))
    try:
        refused = client.post(member_chat.url, headers=admin_headers, json=member_chat.body)
        assert refused.status_code == 409
        assert share_count(client, db_sessionmaker) == 0
    finally:
        if busy == "turn":
            lock.release()
