"""Conversation session storage.

The Messages API is stateless — the full history is resent on every turn — so
the session store is what turns a series of requests into a conversation.

`InMemorySessionStore` is fine for a single process (local dev, one container).
Anything multi-worker needs shared storage: implement `SessionStore` against
Redis and wire it up in `get_session_store()`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .config import get_settings
from .schemas import Message


@dataclass
class Session:
    session_id: str
    messages: list[Message] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    # Module the router last selected for this conversation. Used as the
    # fallback when a later turn is too ambiguous to classify on its own.
    module: str | None = None
    # Durable facts the graph carries across turns, written by
    # update_memory_and_format_node.
    memory: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class SessionStore(ABC):
    @abstractmethod
    async def get_or_create(self, session_id: str | None) -> Session: ...

    @abstractmethod
    async def adopt(
        self,
        session_id: str,
        messages: list[Message],
        module: str | None,
        memory: dict[str, str] | None = None,
    ) -> Session:
        """Recreate a session under an id the caller has been *proven* to own.

        `get_or_create` deliberately refuses to adopt a client-supplied id (see
        there). That is right as a default, but it means a session lost to a
        restart or a TTL cannot be resumed — the next turn silently becomes a
        new conversation, forking the transcript in two.

        This is the escape hatch, and it is only safe because its caller
        verifies ownership against the database first: the id has to name a
        `conversations` row belonging to the authenticated subject. A guessed
        id fails that check and never reaches here.
        """

    @abstractmethod
    async def append(self, session_id: str, message: Message) -> None: ...

    @abstractmethod
    async def set_module(self, session_id: str, module: str) -> None: ...

    @abstractmethod
    async def set_memory(self, session_id: str, memory: dict[str, str]) -> None: ...

    @abstractmethod
    async def reset(self, session_id: str) -> None: ...

    @abstractmethod
    async def get(self, session_id: str) -> Session | None: ...

    @abstractmethod
    async def get_turn_lock(self, session_id: str) -> asyncio.Lock:
        """Return the lock that serialises model turns for one conversation."""


class InMemorySessionStore(SessionStore):
    """Process-local store with TTL expiry and history trimming."""

    def __init__(self, *, ttl_seconds: int, max_messages: int) -> None:
        self._sessions: dict[str, Session] = {}
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds
        self._max_messages = max_messages

    async def get_or_create(self, session_id: str | None) -> Session:
        async with self._lock:
            self._prune_locked()
            if session_id and session_id in self._sessions:
                return self._sessions[session_id]
            # Always mint the id server-side. A client-supplied id for a session
            # we don't know about starts fresh rather than being adopted, so a
            # guessed id can never attach to someone else's conversation.
            new_id = uuid.uuid4().hex
            session = Session(session_id=new_id)
            self._sessions[new_id] = session
            return session

    async def adopt(
        self,
        session_id: str,
        messages: list[Message],
        module: str | None,
        memory: dict[str, str] | None = None,
    ) -> Session:
        async with self._lock:
            self._prune_locked()
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            # Seed with the tail of the persisted transcript, trimmed the same
            # way `append` trims — a resumed session must not re-enter the
            # graph carrying more history than a live one ever would.
            history = list(messages)
            if len(history) > self._max_messages:
                overflow = len(history) - self._max_messages
                history = history[overflow + (overflow % 2) :]
            session = Session(
                session_id=session_id,
                messages=history,
                module=module,
                memory=dict(memory or {}),
            )
            self._sessions[session_id] = session
            return session

    async def get(self, session_id: str) -> Session | None:
        async with self._lock:
            self._prune_locked()
            return self._sessions.get(session_id)

    async def get_turn_lock(self, session_id: str) -> asyncio.Lock:
        # Two devices can submit against the same history. The second turn
        # must wait and then read the first turn's completed messages instead
        # of both models answering the same stale snapshot.
        async with self._lock:
            return self._turn_locks.setdefault(session_id, asyncio.Lock())

    async def append(self, session_id: str, message: Message) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.messages.append(message)
            session.updated_at = time.time()
            if len(session.messages) > self._max_messages:
                # Drop from the front. Keeping the count even preserves the
                # user/assistant alternation the models expect.
                overflow = len(session.messages) - self._max_messages
                session.messages = session.messages[overflow + (overflow % 2) :]

    async def set_module(self, session_id: str, module: str) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.module = module

    async def set_memory(self, session_id: str, memory: dict[str, str]) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.memory = dict(memory)
                session.updated_at = time.time()

    async def reset(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)
            self._turn_locks.pop(session_id, None)

    def _prune_locked(self) -> None:
        cutoff = time.time() - self._ttl
        expired = [k for k, s in self._sessions.items() if s.updated_at < cutoff]
        for key in expired:
            del self._sessions[key]
            self._turn_locks.pop(key, None)


_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """FastAPI dependency. Swap the implementation here to move to Redis."""
    global _store
    if _store is None:
        settings = get_settings()
        _store = InMemorySessionStore(
            ttl_seconds=settings.session_ttl_seconds,
            max_messages=settings.max_history_messages,
        )
    return _store
