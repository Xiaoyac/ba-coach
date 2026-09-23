"""Regression tests for durable state hydration of live sessions."""

import asyncio

import pytest

from app.schemas import Message
from app.session import InMemorySessionStore


@pytest.mark.asyncio
async def test_adopt_hydrates_module_and_memory_without_replacing_live_history():
    store = InMemorySessionStore(ttl_seconds=60, max_messages=40)
    live = await store.get_or_create(None)
    await store.append(live.session_id, Message(role="assistant", content="opening"))

    adopted = await store.adopt(
        live.session_id,
        [Message(role="assistant", content="stale persisted opening")],
        "module_2",
        {"m1_complete": "true"},
    )

    assert adopted is live
    assert adopted.module == "module_2"
    assert adopted.memory == {"m1_complete": "true"}
    assert [message.content for message in adopted.messages] == ["opening"]


@pytest.mark.asyncio
async def test_adopt_does_not_clobber_live_state_or_memory_values():
    store = InMemorySessionStore(ttl_seconds=60, max_messages=40)
    live = await store.get_or_create(None)
    live.module = "module_3"
    live.memory = {"goal": "live value"}
    await store.append(live.session_id, Message(role="user", content="new turn"))

    adopted = await store.adopt(
        live.session_id,
        [],
        "module_2",
        {"goal": "stale value", "durable_marker": "keep"},
    )

    assert adopted.module == "module_3"
    # Existing values are authoritative; newly persisted keys are still
    # imported so a restart snapshot can fill in state not yet held in memory.
    assert adopted.memory == {
        "goal": "live value",
        "durable_marker": "keep",
    }
    assert [message.content for message in adopted.messages] == ["new turn"]


@pytest.mark.asyncio
async def test_adopt_waits_for_active_turn_before_hydrating():
    store = InMemorySessionStore(ttl_seconds=60, max_messages=40)
    live = await store.get_or_create(None)
    turn_lock = await store.get_turn_lock(live.session_id)
    await turn_lock.acquire()
    try:
        pending = asyncio.create_task(
            store.adopt(
                live.session_id,
                [Message(role="assistant", content="must not win")],
                "module_2",
                {"from_db": "true"},
            )
        )
        await asyncio.sleep(0)
        assert not pending.done()

        # Simulate the active graph publishing a fresh message while adopt is
        # waiting.  The persisted snapshot must never replace it afterwards.
        await store.append(
            live.session_id,
            Message(role="assistant", content="active turn result"),
        )
        turn_lock.release()
        adopted = await pending
    finally:
        if turn_lock.locked():
            turn_lock.release()

    assert adopted.module == "module_2"
    assert [message.content for message in adopted.messages] == ["active turn result"]

