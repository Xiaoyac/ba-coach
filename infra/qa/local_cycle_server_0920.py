"""Reproducible local V2 server with a private database and real model calls.

Run with backend/.venv/Scripts/python.exe from the repository root.
Only the provider configuration is read from backend/.env. No production
database, identity, email, push or memory service credentials are imported.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8012)
    parser.add_argument('--db-name', default='local.db')
    parser.add_argument('--import-knowledge', action='store_true')
    args = parser.parse_args()
    if Path(args.db_name).name != args.db_name or not args.db_name.endswith('.db'):
        parser.error('--db-name must be a local .db file name')
    sys.path.insert(0, str(ROOT / 'backend'))
    from dotenv import dotenv_values
    # An explicit allow-list also keeps later additions to .env from changing
    # this runner's database destination.
    provider = {k: v for k, v in dotenv_values(ROOT / 'backend/.env').items()
                if k.startswith('DEEPSEEK_') and v is not None}
    os.environ.update(provider)
    folder = ROOT / '.test-tmp/local-cycle-debug'
    folder.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        DATABASE_URL='sqlite+aiosqlite:///' + (folder / args.db_name).as_posix(),
        DATABASE_SCHEMA_VERSION='v2', STARTUP_DB_MAINTENANCE='false',
        DEFAULT_PROVIDER='deepseek', ROUTER_PROVIDER_NAME='deepseek',
        MEMOS_API_KEY='', MEMOS_BASE_URL='', PA_PUSH_ENABLED='false',
    )
    # A directory without .env prevents BaseSettings from importing unrelated
    # credentials. All allowed settings above are explicit process variables.
    os.chdir(folder)
    from app.db import Base, get_engine, get_sessionmaker, dispose_db
    from app.database_v2_schema import metadata
    import app.models
    from app.models_business import InteractionStatus, RiskMonitoring

    async def initialize():
        async with get_engine().begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[
                t for t in Base.metadata.sorted_tables if t.name not in metadata.tables]))
            for model in (InteractionStatus, RiskMonitoring):
                await conn.run_sync(lambda c, model=model: model.__table__.create(c, checkfirst=True))
        if args.import_knowledge:
            from scripts.import_project_knowledge import _source_paths, _read_source, DEFAULT_KNOWLEDGE_DIR
            from app.knowledge_store import import_knowledge_source
            async with get_sessionmaker()() as db:
                for path, category in _source_paths(DEFAULT_KNOWLEDGE_DIR):
                    await import_knowledge_source(db, name=path.name, category=category,
                        markdown=_read_source(path), updated_by='local-cycle-debug')
                await db.commit()
        await dispose_db()

    asyncio.run(initialize())
    import uvicorn
    uvicorn.run('app.main:app', host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
