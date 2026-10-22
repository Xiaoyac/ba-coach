"""Every chat uses hybrid retrieval; a stale per-chat legacy flag is ignored."""
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import (UserAccount, AccountSettings, Conversation, ConversationKnowledgeSettings,
                        ConversationMediatorNodeSettings)
from app.conversation_knowledge import mediator_enabled_for_conversation


@pytest_asyncio.fixture
async def mode_db():
    engine = create_async_engine('sqlite+aiosqlite://')
    async with engine.begin() as db:
        for model in (UserAccount, AccountSettings, Conversation, ConversationKnowledgeSettings,
                      ConversationMediatorNodeSettings):
            await db.run_sync(model.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await db.execute(insert(UserAccount), {'id': 1, 'username': 'member-fixture',
            'profile_uuid': 'owned-user', 'password_hash': 'unused'})
        await db.execute(insert(Conversation), {'id': 1, 'session_id': 'owned-chat', 'subject_id': 'owned-user'})
        await db.commit()
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['admin', 'user', None])
@pytest.mark.parametrize('saved', [True, False, None])
async def test_effective_mode_for_new_and_existing_chats(mode_db, role, saved):
    async with mode_db() as db:
        if role:
            await db.execute(insert(AccountSettings), {'account_id': 1, 'role': role})
        if saved is not None:
            await db.execute(insert(ConversationKnowledgeSettings), {'conversation_id': 1, 'mediator_enabled': saved})
        enabled = await mediator_enabled_for_conversation(db, session_id='owned-chat', subject_id='owned-user')
        assert enabled is False
        # Unknown or unowned requests are hybrid too.
        for session, subject in [('owned-chat', 'other-user'), ('missing-chat', 'owned-user'), ('owned-chat', None)]:
            assert not await mediator_enabled_for_conversation(db, session_id=session, subject_id=subject)


@pytest.mark.asyncio
async def test_saved_legacy_choice_is_ignored_for_admins_and_members(mode_db):
    async with mode_db() as db:
        await db.execute(insert(AccountSettings), {'account_id': 1, 'role': 'admin'})
        await db.execute(insert(ConversationKnowledgeSettings), {'conversation_id': 1, 'mediator_enabled': True})
        assert not await mediator_enabled_for_conversation(db, session_id='owned-chat', subject_id='owned-user')
        await db.execute(update(AccountSettings).values(role='user'))
        assert not await mediator_enabled_for_conversation(db, session_id='owned-chat', subject_id='owned-user')


@pytest.mark.asyncio
async def test_chat_request_actually_selects_hybrid_backend(mode_db, monkeypatch):
    from app.routes import chat
    from app import retrieval, conversation_reply_mode
    async def thinking(*args, **kwargs): return False
    async def reply_mode(*args, **kwargs): return 'standard'
    monkeypatch.setattr(chat, 'get_sessionmaker', lambda: mode_db)
    monkeypatch.setattr(chat, 'effective_thinking', thinking)
    monkeypatch.setattr(conversation_reply_mode, 'effective_reply_mode', reply_mode)
    hybrid = SimpleNamespace(ranking_mode='astrbot_hybrid')
    monkeypatch.setattr(retrieval, 'get_hybrid_knowledge_base', lambda: hybrid)
    provider = SimpleNamespace(with_thinking=lambda enabled: provider, with_main_reply=lambda: provider)
    context = SimpleNamespace(provider=provider, router_provider=provider, knowledge_base=object())
    state = {}
    await chat._apply_conversation_thinking(context, session_id='owned-chat', subject_id='owned-user', state=state)
    assert context.knowledge_base is hybrid and context.knowledge_mediator_bypass
    assert state['telemetry']['knowledge_mode'] == 'astrbot_hybrid'
