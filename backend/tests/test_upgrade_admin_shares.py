from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import select

from app.models import Conversation, ConversationMessage, ConversationShare
from scripts.upgrade_admin_share_snapshots import upgrade
from test_admin_accounts import admin_headers


def seed_legacy(client, headers, db_sessionmaker, name):
    subject = client.get("/api/auth/me", headers=headers).json()["profile_uuid"]
    async def seed():
        async with db_sessionmaker() as db:
            c = Conversation(subject_id=subject, session_id=name, title=name)
            timestamp = datetime.now(timezone.utc) - timedelta(minutes=10)
            c.messages = [ConversationMessage(position=0, role="user", content="散步", created_at=timestamp),
                          ConversationMessage(position=1, role="assistant", content="走五分钟", created_at=timestamp,
                              reasoning_content="保存的思考", routing_reasoning_content="保存的路由", model_name="saved-model")]
            db.add(c)
            await db.commit()
    client.portal.call(seed)
    result = client.post(f"/api/conversations/{name}/shares", headers=headers)
    assert result.status_code == 201, result.text
    data = result.json()
    async def strip():
        async with db_sessionmaker() as db:
            s = await db.get(ConversationShare, data["id"])
            raw = deepcopy(s.snapshot)
            raw["snapshot_version"] = 1
            raw["messages"] = [{k: v for k, v in m.items() if k in {"id", "role", "content", "created_at", "reply_status"}}
                               for m in raw["messages"]]
            s.snapshot = raw
            await db.commit()
    client.portal.call(strip)
    return data


def test_upgrade_existing_token_admin_only_frozen_and_idempotent(client, admin_headers, auth_headers, db_sessionmaker, tmp_path):
    admin = seed_legacy(client, admin_headers, db_sessionmaker, "old-admin")
    member = seed_legacy(client, auth_headers, db_sessionmaker, "old-member")
    revoked = seed_legacy(client, admin_headers, db_sessionmaker, "revoked-admin")
    path = f"/api/shares/{admin['token']}"
    before = client.get(path).json()
    member_before = client.get(f"/api/shares/{member['token']}").json()
    async def later():
        async with db_sessionmaker() as db:
            c = await db.scalar(select(Conversation).where(Conversation.session_id == "old-admin"))
            c.title = "后来的标题"
            c.messages.extend([ConversationMessage(position=2, role="user", content="后来的秘密"),
                               ConversationMessage(position=3, role="assistant", content="后来回复", reasoning_content="后来思考")])
            s = await db.get(ConversationShare, revoked["id"])
            s.revoked_at = datetime.now(timezone.utc)
            await db.commit()
    client.portal.call(later)
    async def run(apply=False):
        async with db_sessionmaker() as db:
            return await upgrade(db, apply=apply, backup_path=tmp_path / "backup.json")
    preview = client.portal.call(run)
    assert preview["eligible_shares"] == 1 and preview["matched_messages"] == 2
    assert client.get(path).json() == before
    report = client.portal.call(run, True)
    assert report["applied"] is True
    after = client.get(path).json()
    assert after["snapshot_version"] == 2
    assert after["title"] == before["title"] and after["created_at"] == before["created_at"]
    assert len(after["messages"]) == 2
    assert after["messages"][1]["reasoning_content"] == "保存的思考"
    assert after["messages"][1]["routing_reasoning_content"] == "保存的路由"
    assert after["messages"][1]["model_name"] == "saved-model"
    assert after["messages"][1]["request_records"]["requests"][0]["model"] == "saved-model"
    for previous, current in zip(before["messages"], after["messages"]):
        assert {k: current[k] for k in previous} == previous
    assert client.get(f"/api/shares/{member['token']}").json() == member_before
    assert client.get(f"/api/shares/{revoked['token']}").status_code == 404
    backup = json.loads((tmp_path / "backup.json").read_text())
    assert backup["shares"][0]["before"] == before
    assert (tmp_path / "backup.json").stat().st_mode & 0o777 == 0o600
    again = client.portal.call(run, True)
    assert again["eligible_shares"] == 0 and again["already_upgraded"] == 1


def test_mismatched_historical_message_cannot_gain_later_details(client, admin_headers, db_sessionmaker, tmp_path):
    share = seed_legacy(client, admin_headers, db_sessionmaker, "mismatch")
    before = client.get(f"/api/shares/{share['token']}").json()
    async def changed():
        async with db_sessionmaker() as db:
            c = await db.scalar(select(Conversation).where(Conversation.session_id == "mismatch"))
            c.messages[1].content = "不同的回复"
            await db.commit()
    client.portal.call(changed)
    async def run(apply):
        async with db_sessionmaker() as db:
            return await upgrade(db, apply=apply, backup_path=tmp_path / "backup.json")
    assert client.portal.call(run, False)["unmatched_assistants"] == 1
    with pytest.raises(ValueError, match="unmatched historical"):
        client.portal.call(run, True)
    assert not (tmp_path / "backup.json").exists()
    assert client.get(f"/api/shares/{share['token']}").json() == before
