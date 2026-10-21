"""Admin reply ratings name the account that left them."""
from app.models import Conversation, ConversationMessage, ResearchParticipant, UserAccount
from sqlalchemy import select
from test_admin_accounts import admin_headers


def test_admin_feedback_shows_the_author(client, admin_headers, register, db_sessionmaker):
    author = register("feedbackauthor", nickname="小林")
    subject = client.get("/api/auth/me", headers=author).json()["profile_uuid"]

    async def seed():
        async with db_sessionmaker() as db:
            c = Conversation(subject_id=subject, session_id="feedback-author", title="新对话")
            c.messages = [ConversationMessage(position=0, role="user", content="走了十五分钟"),
                          ConversationMessage(position=1, role="assistant", content="那按这次的经历重新理一遍。")]
            db.add(c)
            account = (await db.execute(select(UserAccount).where(UserAccount.profile_uuid == subject))).scalar_one()
            db.add(ResearchParticipant(account_id=account.id, research_id="P042"))
            await db.commit()
            return c.messages[1].id

    message_id = client.portal.call(seed)
    voted = client.put(f"/api/message-feedback/{message_id}", headers=author,
                       json={"rating": "down", "reasons": ["off_topic"], "comment": "也很差"})
    assert voted.status_code == 200, voted.text

    item = client.get("/api/admin/message-feedback", headers=admin_headers).json()["items"][0]
    assert (item["username"], item["display_name"], item["research_id"]) == ("feedbackauthor", "小林", "P042")
    assert client.get("/api/admin/message-feedback", headers=author).status_code == 403
