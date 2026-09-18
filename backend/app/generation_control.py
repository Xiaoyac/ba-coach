"""Authenticated, turn-scoped cancellation for the single-worker session store.

Cancellation tombstones handle a stop arriving before the stream request. Entries
are bounded and expire. Multi-worker deployments need a shared control bus, just
as SessionStore needs shared storage; never route these endpoints to different workers.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


class GenerationStopped(Exception):
    pass


@dataclass
class GenerationControl:
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    claimed: bool = False
    phase: str = "pending"
    updated: float = field(default_factory=time.monotonic)

    def seal(self):
        """Commit boundary: no stop can interrupt memory/DB finalization."""
        if self.stop.is_set():
            raise GenerationStopped()
        self.phase = "finalizing"

    async def run(self, awaitable):
        work = asyncio.ensure_future(awaitable)
        waiter = asyncio.create_task(self.stop.wait())
        try:
            if self.stop.is_set():
                raise GenerationStopped()
            if self.phase == "finalizing":
                return await work
            await asyncio.wait((work, waiter), return_when=asyncio.FIRST_COMPLETED)
            if self.stop.is_set():
                raise GenerationStopped()
            return await work
        finally:
            # Await cancellation so provider/graph finally blocks and DB rollback
            # finish before the stop is acknowledged or the next turn takes the lock.
            if not work.done():
                work.cancel()
            waiter.cancel()
            await asyncio.gather(work, waiter, return_exceptions=True)

    async def iterate(self, iterator):
        iterator = aiter(iterator)
        try:
            while True:
                try:
                    item = await self.run(anext(iterator))
                except StopAsyncIteration:
                    break
                yield item
        finally:
            close = getattr(iterator, "aclose", None)
            if close:
                await close()


class GenerationRegistry:
    def __init__(self):
        self.entries: dict[tuple[str | None, str], GenerationControl] = {}

    def _get(self, owner, generation_id):
        now = time.monotonic()
        for key, entry in list(self.entries.items()):
            if (entry.done.is_set() or not entry.claimed) and now - entry.updated > 600:
                del self.entries[key]
        key = (owner, generation_id)
        if key not in self.entries:
            if sum(1 for k in self.entries if k[0] == owner) >= 100:
                raise ValueError("Too many recent generation controls; retry later")
            if len(self.entries) >= 2000:
                raise ValueError("Too many generation controls; retry later")
            self.entries[key] = GenerationControl()
        return self.entries[key]

    def begin(self, owner, generation_id):
        entry = self._get(owner, generation_id)
        if entry.claimed:
            raise ValueError("Generation id already used")
        entry.claimed = True
        entry.phase = "generating"
        return entry

    async def cancel(self, owner, generation_id):
        entry = self._get(owner, generation_id)
        if entry.phase in {"finalizing", "finished"}:
            return entry.phase
        entry.stop.set()
        if not entry.claimed:
            return "cancelled"
        try:
            await asyncio.wait_for(entry.done.wait(), timeout=8)
        except TimeoutError:
            return "stopping"
        return entry.phase

    def finish(self, entry):
        entry.phase = "cancelled" if entry.stop.is_set() else "finished"
        entry.updated = time.monotonic()
        entry.done.set()


generations = GenerationRegistry()
