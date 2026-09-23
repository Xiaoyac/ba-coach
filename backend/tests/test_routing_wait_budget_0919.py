"""Bounded chat waits must not let a stale post-hoc router race a new turn."""

from __future__ import annotations

import asyncio

import pytest

from app.graph import nodes
from app.config import Settings


@pytest.mark.asyncio
async def test_bounded_wait_cancels_and_removes_stale_router() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_router() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    session_id = "bounded-router-test"
    task = asyncio.create_task(slow_router())
    nodes._routing_tasks[session_id] = task
    try:
        await started.wait()
        completed = await nodes.wait_for_pending_routing(
            session_id, timeout_seconds=0.01, cancel_on_timeout=True
        )
        assert completed is False
        assert session_id not in nodes._routing_tasks
        await asyncio.wait_for(cancelled.wait(), timeout=0.5)
        assert task.cancelled()
    finally:
        nodes._routing_tasks.pop(session_id, None)
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_default_wait_remains_strict_for_state_changing_callers() -> None:
    release = asyncio.Event()

    async def slow_router() -> None:
        await release.wait()

    session_id = "strict-router-test"
    task = asyncio.create_task(slow_router())
    nodes._routing_tasks[session_id] = task
    try:
        waiter = asyncio.create_task(nodes.wait_for_pending_routing(session_id))
        await asyncio.sleep(0)
        assert not waiter.done()
        release.set()
        assert await asyncio.wait_for(waiter, timeout=0.5)
    finally:
        nodes._routing_tasks.pop(session_id, None)
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_schedule_replaces_an_inflight_router(monkeypatch) -> None:
    """A late provider result from an older turn cannot remain scheduled."""
    session_id = "replace-router-test"
    first_cancelled = asyncio.Event()
    second_release = asyncio.Event()
    calls = 0

    async def fake_router(state, _context, *, assistant_message_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                first_cancelled.set()
                raise
        await second_release.wait()

    monkeypatch.setattr(nodes, "_run_background_routing", fake_router)
    state = {"session_id": session_id, "routing_pending": True}
    nodes.schedule_background_routing(state, None, assistant_message_id=1)
    first = nodes._routing_tasks[session_id]
    await asyncio.sleep(0)

    nodes.schedule_background_routing(state, None, assistant_message_id=2)
    second = nodes._routing_tasks[session_id]
    assert second is not first
    await asyncio.wait_for(first_cancelled.wait(), timeout=0.5)

    second_release.set()
    assert await nodes.wait_for_pending_routing(session_id)
    assert second.done() and not second.cancelled()
    assert first.cancelled()


def test_routing_wait_budget_is_bounded_and_configurable() -> None:
    assert Settings(_env_file=None).routing_wait_timeout_seconds == 2.0
    assert Settings(_env_file=None, routing_wait_timeout_seconds=4).routing_wait_timeout_seconds == 4


@pytest.mark.asyncio
async def test_chat_budget_leaves_old_decision_running_until_replacement():
    key = "budget-does-not-drop-transition"
    task = asyncio.create_task(asyncio.sleep(0.05))
    nodes._routing_tasks[key] = task
    try:
        assert not await nodes.wait_for_pending_routing(key, timeout_seconds=0.01)
        assert not task.cancelled() and nodes._routing_tasks[key] is task
        assert await nodes.wait_for_pending_routing(key)
    finally:
        nodes._routing_tasks.pop(key, None)


@pytest.mark.asyncio
async def test_strict_wait_follows_replacement_instead_of_being_cancelled():
    key = "strict-replacement"
    release = asyncio.Event()
    old = asyncio.create_task(asyncio.sleep(10))
    nodes._routing_tasks[key] = old
    waiter = asyncio.create_task(nodes.wait_for_pending_routing(key))
    await asyncio.sleep(0)
    new = asyncio.create_task(release.wait())
    nodes._routing_tasks[key] = new
    old.cancel()
    try:
        await asyncio.sleep(0.01)
        assert not waiter.done()
        release.set()
        assert await asyncio.wait_for(waiter, 0.5)
    finally:
        nodes._routing_tasks.pop(key, None)
        release.set()
        await asyncio.gather(old, new, return_exceptions=True)
