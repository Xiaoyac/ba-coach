"""点赞 carries optional reasons and a comment, like 点踩, from its own reason set."""
from app.models import Conversation, ConversationMessage
from test_admin_accounts import admin_headers


def _seed_reply(client, db_sessionmaker, headers):
    subject = client.get("/api/auth/me", headers=headers).json()["profile_uuid"]

    async def seed():
        async with db_sessionmaker() as db:
            c = Conversation(subject_id=subject, session_id="feedback-up", title="新对话")
            c.messages = [ConversationMessage(position=0, role="user", content="今天走了二十分钟"),
                          ConversationMessage(position=1, role="assistant", content="很好，这一步很具体。")]
            db.add(c)
            await db.commit()
            return c.messages[1].id

    return client.portal.call(seed)


def test_up_vote_saves_reasons_and_comment(client, admin_headers, register, db_sessionmaker):
    author = register("upvoter")
    message_id = _seed_reply(client, db_sessionmaker, author)
    voted = client.put(f"/api/message-feedback/{message_id}", headers=author, json={
        "rating": "up", "reasons": ["practical", "practical", "supportive"], "comment": "  今天就能做到  "})
    assert voted.status_code == 200, voted.text
    assert (voted.json()["reasons"], voted.json()["comment"]) == (["practical", "supportive"], "今天就能做到")

    item = client.get("/api/admin/message-feedback?rating=up", headers=admin_headers).json()["items"][0]
    assert (item["rating"], item["reasons"], item["comment"]) == ("up", ["practical", "supportive"], "今天就能做到")


def test_reasons_must_match_the_rating(client, register, db_sessionmaker):
    author = register("mismatch")
    message_id = _seed_reply(client, db_sessionmaker, author)
    url = f"/api/message-feedback/{message_id}"
    assert client.put(url, headers=author, json={"rating": "up", "reasons": ["off_topic"]}).status_code == 422
    assert client.put(url, headers=author, json={"rating": "down", "reasons": ["practical"]}).status_code == 422
    assert client.put(url, headers=author, json={"rating": "down", "reasons": ["other"]}).status_code == 200
    assert client.put(url, headers=author, json={"rating": None}).json() is None
