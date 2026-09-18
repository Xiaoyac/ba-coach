"""Async SQLAlchemy engine, session factory, and schema bootstrap.

SQLite by default so the project runs with no external service. `DATABASE_URL`
swaps in Postgres (`postgresql+asyncpg://…`) without touching anything else —
the models avoid dialect-specific types for exactly that reason.

    from .db import get_db
    async def endpoint(db: AsyncSession = Depends(get_db)): ...

There is no migration tool wired up. `create_all` creates missing tables but
never alters existing ones, so once this schema has run anywhere you care
about, add Alembic before changing a column.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import DateTime, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from .config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for every ORM model in the app."""


class UTCDateTime(TypeDecorator):
    """A DateTime that round-trips as UTC-aware even on SQLite.

    SQLAlchemy's `DateTime(timezone=True)` is a no-op on SQLite: there is no
    native tz-aware column type there, so the driver stores and returns plain
    naive datetimes regardless of the flag. Every value this app ever writes
    to a timestamp column already comes from `datetime.now(timezone.utc)`, so
    the fix is a matching pair of hooks — drop the tzinfo on the way in (there
    is nothing to store it as), and reattach it, always as UTC, on the way
    out. Skip this and you get exactly the bug it exists to prevent: a naive
    ISO string with no offset, which a browser's `Date` parser reads as *its
    own local time* — every timestamp silently shifted by the viewer's own
    UTC offset. On Postgres `timezone=True` needs no help, but this class
    works there unchanged, so nothing has to fork by dialect.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        kwargs: dict[str, object] = {"echo": settings.database_echo}
        if url.startswith("sqlite"):
            # SQLite hands out one connection per file and serialises writes.
            # Pooling options that make sense for a network database are either
            # ignored or actively harmful here.
            kwargs["connect_args"] = {"check_same_thread": False}
        elif url.startswith("mysql"):
            # A managed MySQL instance (e.g. Aliyun RDS) closes connections
            # that sit idle past its own wait_timeout — pool_recycle retires
            # them from our side first, and pool_pre_ping catches whatever
            # slips through, so a request never dies on a connection the
            # server already dropped.
            kwargs["pool_pre_ping"] = True
            kwargs["pool_recycle"] = 1800
            if settings.database_schema_version == "v2":
                kwargs["connect_args"] = {"init_command": "SET time_zone = '+00:00'"}
        _engine = create_async_engine(url, **kwargs)

        if _engine.url.get_backend_name() == "sqlite":
            # SQLite disables foreign keys per *connection*, so this has to be
            # a connect hook — running the PRAGMA once at startup would only
            # arm the bootstrap connection and every later request would
            # silently skip the ON DELETE CASCADE on activity rows.
            @event.listens_for(_engine.sync_engine, "connect")
            def _enable_sqlite_fks(dbapi_conn, _record) -> None:  # noqa: ANN001
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,  # let handlers read attributes after commit
            autoflush=False,
        )
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session scoped to one request."""
    async with get_sessionmaker()() as session:
        yield session


async def init_db() -> None:
    """Create missing tables. Called once from the app lifespan."""
    # Imported for the side effect of registering the models on Base.metadata;
    # without this create_all sees an empty metadata object.
    from . import models  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.url.get_backend_name() == "sqlite":
            # The legacy clinical tables are externally managed in production,
            # so they must never be emitted against MySQL/Postgres.  SQLite is
            # the dependency-free local-development database, though, and an
            # empty file is otherwise unusable: registration/bootstrap inserts
            # a user_profile row immediately.  Creating the portable mappings
            # here keeps the documented fresh-clone workflow working while the
            # backend-name guard preserves the production ownership boundary.
            from .models_business import BizBase

            await conn.run_sync(BizBase.metadata.create_all)
    logger.info("database ready at %s", engine.url.render_as_string(hide_password=True))


async def dispose_db() -> None:
    """Close pooled connections on shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
