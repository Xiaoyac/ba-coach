"""Shared test fixtures.

A stub provider stands in for the real SDKs, so the whole suite runs with no
API key and no network. The `client` fixture likewise redirects `get_db` at a
throwaway in-memory SQLite database — see that fixture for why that is not
optional.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base, get_db
from app.models_business import BizBase
from app.graph.state import GraphContext
from app.main import app
from app.providers.base import Completion, LLMProvider, StreamDelta, SystemPrompt
from app.retrieval import StubKnowledgeBase
from app.routes import chat as chat_route
from app.schemas import Message
from app.session import InMemorySessionStore, get_session_store


class StubProvider(LLMProvider):
    """Records what it was handed; echoes how much history it saw."""

    name = "stub"
    model = "stub-1"

    def __init__(self) -> None:
        self.seen: list[list[Message]] = []
        self.systems: list[SystemPrompt] = []
        self.classify_calls: list[str] = []
        self.classify_result: str | None = None
        self.route_calls: list[str] = []
        self.route_systems: list[str] = []
        self.route_max_tokens_calls: list[int | None] = []
        # "" (the default) mirrors a router that produced nothing usable —
        # decide_target_module() falls back to keeping the current module.
        self.route_result: str = ""
        # `route()` is shared by four different callers (module router,
        # summarizer, clinical extractor, risk screener). One canned answer
        # for all of them means a test cannot set up an extraction without
        # also feeding the router garbage — so these are answered separately,
        # dispatched on the system prompt each caller sends.
        self.extraction_result: str = "{}"
        self.risk_result: str = "{}"
        self.reasoning_result: str = ""
        self.route_reasoning_result: str = "模块路由测试思考"
        self.fail_with: Exception | None = None

    def _record(self, system: SystemPrompt, messages: list[Message]) -> None:
        self.seen.append(list(messages))
        self.systems.append(system)

    async def complete(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> Completion:
        self._record(system, messages)
        if self.fail_with:
            raise self.fail_with
        return Completion(
            text=f"saw {len(messages)} messages",
            model="stub-1",
            usage={"input_tokens": 1, "output_tokens": 1},
            reasoning_content=self.reasoning_result,
            finish_reason="stop",
            request_id="stub-request-1",
        )

    async def stream(
        self, *, system: SystemPrompt, messages: list[Message]
    ) -> AsyncIterator[StreamDelta]:
        self._record(system, messages)
        if self.reasoning_result:
            yield StreamDelta(kind="reasoning", text=self.reasoning_result)
        for chunk in ("he", "ll", "o"):
            yield StreamDelta(kind="content", text=chunk)
            if self.fail_with:
                raise self.fail_with
        yield StreamDelta(
            kind="usage",
            usage={"input_tokens": 1, "output_tokens": 2, "reasoning_tokens": 0},
            finish_reason="stop",
            request_id="stub-request-1",
        )

    async def classify(
        self, *, system: str, user: str, allowed: Sequence[str], default: str
    ) -> str:
        self.classify_calls.append(user)
        return self.classify_result or default

    async def route(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> str:
        self.route_calls.append(user)
        self.route_systems.append(system)
        self.route_max_tokens_calls.append(max_tokens)
        # Matched on the opening line each prompt commits to, so a reworded
        # body does not silently reroute a test's stubbed answer.
        if "风险信号检测器" in system:
            return self.risk_result
        if "信息抽取器" in system:
            return self.extraction_result
        return self.route_result

    async def route_with_reasoning(
        self, *, system: str, user: str, max_tokens: int | None = None
    ) -> Completion:
        # Reuse route() so all existing call recording and prompt-specific
        # canned answers remain valid while exposing a deterministic thought.
        text = await self.route(
            system=system,
            user=user,
            max_tokens=max_tokens,
        )
        return Completion(
            text=text,
            model="stub-router-1",
            reasoning_content=self.route_reasoning_result,
        )


@pytest.fixture
def approved_mediator(provider, monkeypatch):
    """Explicit successful mediator for retrieval-to-prompt integration tests."""
    import json
    async def approve(**kwargs):
        payload = json.loads(kwargs["user"])
        selections = [{"id": x["id"], "quote": x["text"][:40],
            "application": "Use the selected evidence only as background."} for x in payload["knowledge"][:2]]
        return Completion(text=json.dumps({"decision": "use" if selections else "no_match",
            "selections": selections, "note": "" if selections else "No candidates."}), model="approved-test-mediator")
    monkeypatch.setattr(provider, "route_detailed", approve)


class StubMemos:
    """Records save_memo/retrieve_recent_memos calls; no network."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, str]] = []
        self.retrieve_calls: list[str] = []
        self.retrieve_result: list[str] = []

    async def save_memo(self, user_id: str, summary_text: str) -> bool:
        self.saved.append((user_id, summary_text))
        return True

    async def retrieve_recent_memos(
        self, user_id: str, limit: int = 5, *, query: str = ""
    ) -> list[str]:
        self.retrieve_calls.append(user_id)
        return self.retrieve_result


@pytest.fixture
def memos() -> StubMemos:
    return StubMemos()


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch, tmp_path):
    """Clear cached settings, and point the whole suite at a local database.

    `DATABASE_URL` in `.env` names a production MySQL instance. Overriding
    `get_db` (see `client` below) is not enough on its own: `TestClient(app)`
    runs the app's lifespan, and that calls `init_db()` against
    `app.db.get_engine()` directly. Without this every such test opened a real
    network connection to that server — slow enough to push the suite past its
    timeout, and pointed at production, which is the part that actually
    matters.

    The module-level engine/sessionmaker cached in `app.db` are reset too, or
    the first test's engine would be reused by every later one regardless of
    what this fixture sets.

    `.env` is unhooked entirely rather than overriding one key at a time.
    Patching just `DATABASE_URL` left every *other* setting inherited from the
    developer's real file — which is how a test asserting "no admin key is
    configured" started failing the moment a real `ADMIN_RESET_KEY` was added
    to `.env`. Tests assert on defaults plus whatever they set themselves;
    anything read from a file that is not in version control makes a run
    depend on the machine it is on.
    """
    from app import db as db_module
    from app.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(db_module, "_engine", None, raising=False)
    monkeypatch.setattr(db_module, "_sessionmaker", None, raising=False)
    get_settings.cache_clear()
    yield
    db_module._engine = None
    db_module._sessionmaker = None
    get_settings.cache_clear()


@pytest.fixture
def provider() -> StubProvider:
    return StubProvider()


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore(ttl_seconds=60, max_messages=40)


@pytest.fixture
def context(provider: StubProvider, store: InMemorySessionStore) -> GraphContext:
    """Runtime context for driving the graph directly, without HTTP."""
    return GraphContext(
        provider=provider,
        router_provider=provider,
        store=store,
        knowledge_base=StubKnowledgeBase(),
        settings=get_settings(),
        stream=False,
    )


@pytest_asyncio.fixture
async def db_sessionmaker():
    """A throwaway in-memory SQLite database, fresh per test.

    StaticPool + a single shared connection because SQLite's ":memory:" is
    scoped to the connection: with a normal pool, the connection that ran
    `create_all` would be returned to the pool and a later checkout could get
    a different, empty database.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # BizBase is created *here only*. In the app it is never passed to
        # create_all — those 7 tables are owned by another system and this
        # code must never create, alter or drop them (see models_business).
        # A throwaway SQLite file is the one place that guarantee is not at
        # stake, and registration genuinely inserts a `user_profile` row, so
        # the tables have to exist for any account test to run at all.
        await conn.run_sync(BizBase.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest.fixture
def client(monkeypatch, provider: StubProvider, store: InMemorySessionStore, db_sessionmaker):
    """TestClient wired to stubs and an isolated database.

    Overriding `get_db` is not cosmetic: `_persist_turn` in routes/chat.py
    writes each turn to `conversations`/`conversation_messages`, so without
    this every request made through this fixture would land real rows in
    whatever `DATABASE_URL` points at — which is a production MySQL instance.
    (It only stayed unnoticed for as long as it did because `_persist_turn`
    no-ops when the caller sends no `X-Subject-Id`, and no test sent one until
    the subject-identity tests were added.)
    """
    monkeypatch.setattr(chat_route, "get_provider", lambda name=None: provider)
    app.dependency_overrides[get_session_store] = lambda: store

    async def _override_get_db() -> AsyncIterator:
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    # The app reaches the database two ways: request-scoped through `get_db`,
    # and through `get_sessionmaker()` for work that outlives the request (the
    # graph's clinical writes and profile load). In production both are the
    # same database. Overriding only the dependency left the second path
    # pointing at whatever `DATABASE_URL` named — a *different*, empty file —
    # so anything the graph loaded came back missing or errored, while every
    # assertion made through HTTP looked fine. Point both at one database.
    from app import db as db_module

    monkeypatch.setattr(db_module, "_sessionmaker", db_sessionmaker)

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def register(client: TestClient):
    """Factory: create an account, return auth headers for it.

    Registration is the only way to get a usable identity now that
    `X-Subject-Id` is gone — `subject_id` is `user_profile.uuid`, minted
    server-side, so a test can no longer just invent one.
    """

    next_tag = 10000

    def _register(
        username: str = "tester", password: str = "correct-horse-battery", **profile
    ) -> dict[str, str]:
        nonlocal next_tag
        tag = profile.pop("tag", f"{next_tag:05d}")
        next_tag += 1
        from app.birth_dates import today
        requested_age = profile.pop("age", 28)
        birthday = profile.pop("birth_date", today().replace(year=today().year - requested_age, month=1, day=1).isoformat())
        response = client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": password,
                "email": profile.pop("email", f"{username.lower()}@example.com"),
                "nickname": profile.pop("nickname", username),
                "tag": tag,
                "birth_date": birthday,
                **profile,
            },
        )
        assert response.status_code == 201, response.text
        return {"Authorization": f"Bearer {response.json()['token']}"}

    return _register


@pytest.fixture
def auth_headers(register) -> dict[str, str]:
    """Headers for a default signed-in account."""
    return register()
