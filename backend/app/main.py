"""FastAPI application entry point.

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import get_settings
from .conversation_store import backfill_opening_messages
from .db import dispose_db, get_sessionmaker, init_db
from .providers import configured_providers
from .retrieval import warm_knowledge_base
from .routes import (
    admin_accounts_router,
    admin_knowledge_router,
    admin_prompts_router,
    admin_sandbox_router,
    admin_evaluations_router,
    assessment_router,
    auth_router,
    chat_router,
    conversation_router,
    issue_reports_router,
    profile_router,
)
from .schemas import HealthResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # `create_all` only adds missing tables — it never alters existing ones.
    # Reach for Alembic before changing a column that already exists anywhere
    # you care about.
    maintenance_enabled = get_settings().startup_db_maintenance
    if get_settings().database_schema_version == "v2":
        maintenance_enabled = False
        from sqlalchemy import select
        from .database_v2_schema import metadata as v2_schema
        async with get_sessionmaker()() as db:
            # Fail closed on a half-migrated deployment, instead of serving a
            # healthy homepage over incompatible business tables.
            for table in v2_schema.tables.values():
                await db.execute(select(table).limit(0))
    if maintenance_enabled:
        await init_db()
    # Conversations created before the opening turn moved server-side are
    # repaired once and then remain ordinary durable transcripts.
    if maintenance_enabled:
        async with get_sessionmaker()() as db:
            repaired = await backfill_opening_messages(db)
            if repaired:
                logging.getLogger(__name__).info(
                    "backfilled opening message into %d conversation(s)", repaired
                )
    else:
        logging.getLogger(__name__).info("startup database maintenance disabled")
    try:
        chunks = await warm_knowledge_base()
        logging.getLogger(__name__).info(
            "knowledge base ready: %d indexed chunk(s)", chunks
        )
    except Exception:  # noqa: BLE001
        # Knowledge is an enhancement, not a reason to make login/chat fail at
        # startup. Search will retry lazily on the first turn.
        logging.getLogger(__name__).exception("knowledge base warmup failed")
    yield
    await dispose_db()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Psychology AI API",
        description="FastAPI backend for the psychology AI workflow migrated from Coze.",
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # `or None`: an empty string from .env (the documented way to turn
        # this off) must not become an empty *pattern* — `re.match("", x)`
        # matches everything at position 0, which would flip "disabled" into
        # "allow every origin".
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth first: every other router's identity dependency resolves against
    # the sessions this one mints.
    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(admin_accounts_router, prefix=settings.api_prefix)
    app.include_router(admin_knowledge_router, prefix=settings.api_prefix)
    app.include_router(admin_prompts_router, prefix=settings.api_prefix)
    app.include_router(admin_sandbox_router, prefix=settings.api_prefix)
    app.include_router(admin_evaluations_router, prefix=settings.api_prefix)
    app.include_router(chat_router, prefix=settings.api_prefix)
    app.include_router(assessment_router, prefix=settings.api_prefix)
    app.include_router(conversation_router, prefix=settings.api_prefix)
    app.include_router(issue_reports_router, prefix=settings.api_prefix)
    app.include_router(profile_router, prefix=settings.api_prefix)
    from .routes.program import router as program_router
    app.include_router(program_router, prefix=settings.api_prefix)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok", version=__version__, providers=configured_providers()
        )

    return app


app = create_app()
