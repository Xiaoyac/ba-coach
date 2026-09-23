"""Regression coverage for rehydrating a session created before its V2 state."""

import pytest

from app.session import InMemorySessionStore


@pytest.mark.asyncio
async def test_adopt_populates_module_on_existing_default_session():
    store = InMemorySessionStore(ttl_seconds=60, max_messages=40)
    live = await store.get_or_create(None)

    # Conversation creation seeds the in-memory object first, then adopts the
    # durable runtime row.  The old implementation returned ``live`` unchanged
    # here, so the next request fell back to M1 even when the DB said M2.
    adopted = await store.adopt(live.session_id, [], "module_2", {"active_goal": "g1"})

    assert adopted is live
    assert adopted.module == "module_2"
    assert adopted.memory == {"active_goal": "g1"}


@pytest.mark.asyncio
async def test_adopt_does_not_replace_a_live_module_with_stale_persisted_state():
    store = InMemorySessionStore(ttl_seconds=60, max_messages=40)
    live = await store.get_or_create(None)
    await store.set_module(live.session_id, "module_2")

    adopted = await store.adopt(live.session_id, [], "module_1", {"old": "value"})

    assert adopted.module == "module_2"
    assert adopted.memory == {"old": "value"}
