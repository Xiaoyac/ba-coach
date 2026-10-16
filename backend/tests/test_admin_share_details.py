"""Admin publication freezes debug panels; role and legacy boundaries stay explicit."""
from copy import deepcopy

from sqlalchemy import select

from app.models import AccountSettings, AIExecutionEvent, Conversation, ConversationMessage, ConversationShare, UserAccount
from test_admin_accounts import admin_headers


def test_admin_share_freezes_all_panels_without_live_access(client, admin_headers, auth_headers, db_sessionmaker, provider):
    subject = client.get("/api/auth/me", headers=admin_headers).json()["profile_uuid"]

    async def seed():
        async with db_sessionmaker() as db:
            c = Conversation(subject_id=subject, session_id="admin-debug", title="管理员分享")
            c.messages = [
                ConversationMessage(position=0, role="user", content="去散步"),
                ConversationMessage(position=1, role="assistant", content="<think>嵌入思考</think>先走五分钟。",
                    reasoning_content="回复思考", model_name="reply-model",
                    routing_reasoning_content="路由思考", router_model_name="router-model",
                    provider_request_id="main-request", main_generation_duration_ms=200,
                    time_to_first_reasoning_token_ms=10, time_to_first_content_token_ms=60, router_duration_ms=80),
            ]
            db.add(c)
            await db.flush()
            metadata = {"knowledge_references": {
                "available": True, "mediator_guidance": "中介建议", "mediator_reasoning_content": "中介思考",
                "mediator_model": "mediator-model", "mediator_duration_ms": 35,
                "recalled": [{"id": "chunk", "source": "教材", "text": "知识片段"}],
                "provided": [{"id": "chunk", "source": "教材", "text": "知识片段"}],
            }, "private_profile": "UNRELATED_PROFILE"}
            for stage, request_id, data in [
                ("main_generation", "main-request", metadata),
                ("module_router", "router-request", {"user_message_id": c.messages[0].id}),
            ]:
                db.add(AIExecutionEvent(subject_id=subject, conversation_id=c.id, session_id=c.session_id,
                    assistant_message_id=c.messages[1].id, stage=stage, provider="stub",
                    model_name=stage, provider_request_id=request_id, event_metadata=data))
            await db.commit()

    client.portal.call(seed)
    assert client.post("/api/conversations/admin-debug/shares", headers=auth_headers).status_code == 404
    created = client.post("/api/conversations/admin-debug/shares", headers=admin_headers)
    assert created.status_code == 201, created.text
    assert created.json()["snapshot_version"] == 2
    path = f"/api/shares/{created.json()['token']}"
    response = client.get(path)
    assert response.status_code == 200
    before = response.json()
    m = before["messages"][1]
    assert [item["id"] for item in before["messages"]] == [1, 2]
    assert "reply_to_message_id" not in m
    assert m["content"] == "先走五分钟。"
    owned = client.get("/api/conversations/admin-debug", headers=admin_headers).json()["messages"][1]
    assert m["reasoning_content"] == owned["reasoning_content"] == "嵌入思考"
    assert m["routing_reasoning_content"] == "路由思考"
    assert m["model_name"] == "reply-model" and m["router_model_name"] == "router-model"
    assert m["timing"] == {"reply_thinking_ms": 50, "reply_generation_ms": 200, "router_processing_ms": 80}
    assert m["knowledge_references"]["mediator_guidance"] == "中介建议"
    assert m["knowledge_references"]["mediator_reasoning_content"] == "中介思考"
    assert m["knowledge_references"]["provided"][0]["text"] == "知识片段"
    assert {r["request_id"] for r in m["request_records"]["requests"]} == {"main-request", "router-request"}
    assert "UNRELATED_PROFILE" not in response.text
    assert client.get(path, headers=auth_headers).json() == before
    assert client.get("/api/conversations/admin-debug").status_code == 401
    assert provider.seen == [] and provider.route_calls == []

    async def mutate():
        async with db_sessionmaker() as db:
            c = (await db.execute(select(Conversation).where(Conversation.session_id == "admin-debug"))).scalar_one()
            c.title = "后来改名"
            c.messages[1].reasoning_content = "后来思考"
            role = await db.scalar(select(AccountSettings).join(UserAccount).where(UserAccount.profile_uuid == subject))
            role.role = "user"
            share = await db.get(ConversationShare, created.json()["id"])
            assert share.snapshot == before
            await db.commit()

    client.portal.call(mutate)
    assert client.get(path).json() == before
    # Neither a stale admin client nor body/query flags may grant publication.
    member = client.post("/api/conversations/admin-debug/shares?include_diagnostics=true", headers=admin_headers,
                         json={"snapshot_version": 2, "include_diagnostics": True, "role": "admin"})
    assert member.status_code == 201
    public = client.get(f"/api/shares/{member.json()['token']}").json()
    assert public["snapshot_version"] == 1
    assert all(set(row) == {"id", "role", "content", "created_at", "reply_status"} for row in public["messages"])

    # Historical rich v1 snapshots must not silently acquire debug visibility.
    async def legacy():
        async with db_sessionmaker() as db:
            share = await db.get(ConversationShare, created.json()["id"])
            data = deepcopy(share.snapshot)
            data["snapshot_version"] = 1
            share.snapshot = data
            await db.commit()
    client.portal.call(legacy)
    old = client.get(path).json()
    assert all(set(row) == {"id", "role", "content", "created_at", "reply_status"} for row in old["messages"])
