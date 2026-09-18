"""Bounded, exact, process-local retrieval cache. Never stores model replies.

No disk/Redis writes and no plaintext queries or identities in keys/metrics.
The caller supplies corpus/configuration/scope versions and checks freshness.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from collections import Counter, OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .retrieval import KnowledgeChunk


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[KnowledgeChunk, ...] = ()
    status: str = "completed"
    cacheable: bool = True
    model_calls: int = 0


@dataclass
class _Entry:
    result: SearchResult
    expires_at: float
    size: int
    identity: str


@dataclass
class _Flight:
    task: asyncio.Task
    identity: str
    waiters: int = 0


class ExactRetrievalCache:
    def __init__(self, *, max_entries=256, max_bytes=8 * 1024 * 1024,
                 ttl_seconds=300.0, empty_ttl_seconds=45.0, clock=monotonic):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self.empty_ttl_seconds = empty_ttl_seconds
        self._clock = clock
        self._salt = secrets.token_bytes(32)
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._flights: dict[str, _Flight] = {}
        self._bytes = 0
        self._generation = 0
        self._counts: Counter = Counter()
        self._miss_reasons: Counter = Counter()
        self._invalidation_reasons: Counter = Counter()
        # Bounded diagnostic fingerprints only; no query/user text. Losing a
        # fingerprint must be reported as unknown, not invented as a new query.
        self._history: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def _remember(self, identity, reason):
        now = self._clock()
        while self._history and next(iter(self._history.values()))[1] <= now:
            self._history.popitem(last=False)
        self._history[identity] = (reason, now + max(600, self.ttl_seconds * 2))
        self._history.move_to_end(identity)
        while len(self._history) > max(1, min(self.max_entries * 4, 40000)):
            self._history.popitem(last=False)

    def key(self, parts) -> str:
        # Exact serialization: never strip negation, numbers, punctuation or
        # whitespace that could change the effective retrieval input.
        payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac.new(self._salt, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def clear(self, reason="manual") -> None:
        reason = reason if reason in {"manual", "corpus_changed", "content_changed", "revision_error"} else "manual"
        for entry in self._entries.values():
            self._remember(entry.identity, "invalidated")
        for flight in self._flights.values():
            self._remember(flight.identity, "invalidated")
        self._generation += 1
        self._entries.clear()
        self._bytes = 0
        # Old waiters finish normally; the caller will reject the old corpus.
        # Detach flights so new requests never join pre-invalidation work.
        self._flights.clear()
        self._counts["invalidations"] += 1
        self._invalidation_reasons[reason] += 1

    def stats(self) -> dict:
        self._expire()
        return {**self._counts, "entries": len(self._entries),
                "payload_bytes": self._bytes, "inflight": len(self._flights),
                "miss_reasons": dict(self._miss_reasons),
                "invalidation_reasons": dict(self._invalidation_reasons)}

    def record_served_hit(self, saved_model_calls=0) -> None:
        # Count useful hits only AFTER the caller's final corpus check.
        self._counts["served_hits"] += 1
        self._counts["saved_model_calls"] += saved_model_calls

    def record_served_coalesced(self, saved_model_calls=0) -> None:
        self._counts["coalesced_saved_model_calls"] += saved_model_calls

    def _drop(self, key, reason="evicted"):
        entry = self._entries.pop(key)
        self._bytes -= entry.size
        self._remember(entry.identity, reason)

    def _expire(self):
        now = self._clock()
        for key, entry in list(self._entries.items()):
            if entry.expires_at <= now:
                self._drop(key, "expired")
                self._counts["expired"] += 1

    def _put(self, key, result, identity):
        if not result.cacheable:
            self._counts["uncacheable"] += 1
            self._remember(identity, "uncacheable")
            return
        # Payload byte budget plus an allowance for entry/key bookkeeping;
        # max_entries separately bounds Python object overhead.
        size = 256 + sum(len((hit.id + hit.text + hit.source).encode("utf-8"))
                         for hit in result.hits)
        if size > self.max_bytes or self.max_entries <= 0:
            self._counts["oversized"] += 1
            self._remember(identity, "oversized")
            return
        self._expire()
        if key in self._entries:
            self._drop(key)
        while self._entries and (len(self._entries) >= self.max_entries
                                 or self._bytes + size > self.max_bytes):
            self._drop(next(iter(self._entries)))
            self._counts["evictions"] += 1
        ttl = self.ttl_seconds if result.hits else self.empty_ttl_seconds
        self._entries[key] = _Entry(result, self._clock() + ttl, size, identity)
        self._bytes += size
        self._remember(identity, "key_changed")

    async def get_or_compute(self, key: str, compute: Callable[[], Awaitable[SearchResult]],
                             *, identity: str | None = None, diagnostics: dict | None = None):
        identity = identity or key
        self._expire()
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
            self._counts["hits"] += 1
            if diagnostics is not None:
                diagnostics["cache_reason"] = "exact_match"
            return entry.result, "hit"
        flight = self._flights.get(key)
        if flight is None:
            if len(self._flights) >= self.max_entries:
                self._counts["bypass_capacity"] += 1
                if diagnostics is not None:
                    diagnostics["cache_reason"] = "bypass_capacity"
                return await compute(), "bypass_capacity"
            generation = self._generation
            previous = self._history.get(identity)
            reason = previous[0] if previous and previous[1] > self._clock() else "first_or_untracked"
            self._miss_reasons[reason] += 1
            if diagnostics is not None:
                diagnostics["cache_reason"] = reason

            async def run():
                try:
                    result = await compute()
                except asyncio.CancelledError:
                    if generation == self._generation:
                        self._remember(identity, "cancelled")
                    raise
                except Exception:
                    if generation == self._generation:
                        self._remember(identity, "compute_error")
                    raise
                if generation == self._generation:
                    self._put(key, result, identity)
                return result

            task = asyncio.create_task(run())
            # Consume exceptions even if all waiters have been cancelled.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            flight = _Flight(task, identity)
            self._flights[key] = flight
            access = "miss"
            self._counts["misses"] += 1
        else:
            access = "coalesced"
            self._counts["coalesced"] += 1
            if diagnostics is not None:
                diagnostics["cache_reason"] = "inflight_match"
        flight.waiters += 1
        try:
            # One disconnected request must not cancel another request's work.
            return await asyncio.shield(flight.task), access
        finally:
            flight.waiters -= 1
            if flight.waiters == 0:
                if self._flights.get(key) is flight:
                    self._flights.pop(key)
                if not flight.task.done():
                    flight.task.cancel()
                    self._counts["cancelled_flights"] += 1
