"""Member and legacy tokens retain the transcript-only publication contract."""
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.models import Conversation, ConversationMessage, ConversationShare
from app.share_schemas import SharedMessage
from test_admin_accounts import admin_headers

PRIVATE = {
    "reasoning_content": "SECRET_THOUGHT",
    "routing_reasoning_content": "SECRET_ROUTER",
    "model_name": "SECRET_MODEL",
    "router_model_name": "SECRET_ROUTER_MODEL",
    "request_records": {"debug": "SECRET_REQUEST"},
    "knowledge_references": {"chunks": ["SECRET_KNOWLEDGE"]},
    "timing": {"reply_thinking_ms": 888},
    "profile": {"birthday": "SECRET_PROFILE"},
    "future_private_field": "SECRET_FUTURE",
}


@pytest.mark.parametrize("owner", ["auth_headers"])
def test_new_and_existing_shares_publish_only_transcript(client, request, owner, db_sessionmaker):
    headers = request.getfixturevalue(owner)
    subject = client.get("/api/auth/me", headers=headers).json()["profile_uuid"]

    async def seed():
        async with db_sessionmaker() as db:
            row = Conversation(subject_id=subject, session_id="privacy-chat", title="散步计划")
            row.messages = [
                ConversationMessage(position=0, role="user", content="今天想散步"),
                ConversationMessage(position=1, role="assistant", content="<think>SECRET_EMBEDDED</think>可以先走五分钟。",
                    reasoning_content="SECRET_THOUGHT", model_name="SECRET_MODEL",
                    routing_reasoning_content="SECRET_ROUTER", router_model_name="SECRET_ROUTER_MODEL",
                    provider_request_id="SECRET_REQUEST", main_generation_duration_ms=888),
            ]
            db.add(row)
            await db.commit()

    client.portal.call(seed)
    created = client.post("/api/conversations/privacy-chat/shares", headers=headers)
    assert created.status_code == 201, created.text
    token = created.json()["token"]

    async def stored_and_make_legacy():
        async with db_sessionmaker() as db:
            share = (await db.execute(select(ConversationShare))).scalar_one()
            stored = deepcopy(share.snapshot)
            legacy = deepcopy(stored)
            legacy["private_metadata"] = "SECRET_ACCOUNT"
            legacy["messages"][1].update(PRIVATE)
            legacy["messages"][1]["content"] = "<thinking>SECRET_OLD</thinking>可以先走五分钟。<think>SECRET_UNCLOSED"
            share.snapshot = legacy
            await db.commit()
            return stored

    stored = client.portal.call(stored_and_make_legacy)
    for snapshot in [stored, client.get(f"/api/shares/{token}").json()]:
        assert set(snapshot) == {"snapshot_version", "title", "created_at", "messages"}
        assert snapshot["title"] == "散步计划"
        assert [m["id"] for m in snapshot["messages"]] == [1, 2]
        assert [m["content"] for m in snapshot["messages"]] == ["今天想散步", "可以先走五分钟。"]
        assert all(set(m) == {"id", "role", "content", "created_at", "reply_status"} for m in snapshot["messages"])
        assert "SECRET" not in str(snapshot)
    public = client.get(f"/api/shares/{token}")
    assert public.headers["cache-control"] == "private, no-store"
    assert client.post("/api/conversations/privacy-chat/shares").status_code == 401


@pytest.mark.parametrize("content,expected", [
    ("<think>response SECRET</think>", ""),
    ("<thinking>SECRET</thinking>正文<think>SECRET", "正文"),
    ('<message datetime="260930-12:00"><think>SECRET</think>正文</message>', "正文"),
    ('<tool_call>SECRET</tool_call>', ""),
    ('{"chat_reply":"正文","reasoning":"SECRET_JSON"}', "正文"),
    ('```json\n{"chat_reply":"<think>SECRET</think>正文","reasoning":"SECRET_JSON"}\n```', "正文"),
    ('{"chat_reply":"正文","reasoning":"SECRET_TRUNCATED', ""),
    ('{"reasoning":"SECRET_TRUNCATED","chat_reply":"正文', ""),
    ('{"chat_reply":null,"reasoning":"SECRET_JSON"}', ""),
    ('{"chat_reply":{"reasoning":"SECRET_JSON"}}', ""),
    ('{"activity":"散步"}', '{"activity":"散步"}'),
])
def test_public_reply_does_not_promote_thoughts(content, expected):
    assert SharedMessage(id=1, role="assistant", content=content, **PRIVATE).content == expected


def test_share_preserves_user_authored_structured_text():
    content = '{"chat_reply":"<think>用户自己输入的例子</think>","note":"保留原文"}'
    assert SharedMessage(id=1, role="user", content=content).content == content


def test_member_profile_hides_models_and_rejects_changes(client, auth_headers, monkeypatch):
    for method in (lambda: client.get("/api/profile", headers=auth_headers),
                   lambda: client.patch("/api/profile", headers=auth_headers, json={"nickname": "新昵称"})):
        result = method()
        assert result.status_code == 200, result.text
        assert result.json()["can_manage_models"] is False
        assert "preferred_provider" not in result.json()
        assert "available_providers" not in result.json()
    for provider in ("doubao", "deepseek", None):
        response = client.patch("/api/profile", headers=auth_headers,
            json={"preferred_provider": provider, "nickname": "不得保存"})
        assert response.status_code == 403
    assert client.get("/api/profile", headers=auth_headers).json()["nickname"] == "新昵称"
    # The guard must run before the alternate schema write path too.
    from app import v2_profile
    monkeypatch.setattr(v2_profile, "enabled", lambda: True)
    async def forbidden(*args):
        raise AssertionError("member must never reach model preference persistence")
    monkeypatch.setattr(v2_profile, "patch", forbidden)
    assert client.patch("/api/profile", headers=auth_headers, json={"preferred_provider": "doubao"}).status_code == 403


@pytest.mark.parametrize("identity", ["auth_headers", "admin_headers"])
def test_alternate_profile_reads_and_edits_apply_same_visibility(client, request, identity, monkeypatch):
    headers = request.getfixturevalue(identity)
    from app import v2_profile
    from app.schemas import ProfileOut
    async def result(*args):
        return ProfileOut(preferred_provider="doubao", available_providers={"deepseek": True, "doubao": True})
    monkeypatch.setattr(v2_profile, "enabled", lambda: True)
    monkeypatch.setattr(v2_profile, "read", result)
    monkeypatch.setattr(v2_profile, "patch", result)
    for response in [client.get("/api/profile", headers=headers),
                     client.patch("/api/profile", headers=headers, json={"nickname": "名字"})]:
        assert response.status_code == 200, response.text
        is_admin = identity == "admin_headers"
        assert response.json()["can_manage_models"] == is_admin
        assert ("preferred_provider" in response.json()) == is_admin
        assert ("available_providers" in response.json()) == is_admin
