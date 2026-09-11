from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import main


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_startup_maintenance_switch(monkeypatch, enabled):
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(startup_db_maintenance=enabled, database_schema_version="legacy"))
    init = AsyncMock()
    backfill = AsyncMock(return_value=0)
    session = MagicMock()
    warm = AsyncMock(return_value=10)
    dispose = AsyncMock()
    monkeypatch.setattr(main, "init_db", init)
    monkeypatch.setattr(main, "backfill_opening_messages", backfill)
    monkeypatch.setattr(main, "get_sessionmaker", session)
    monkeypatch.setattr(main, "warm_knowledge_base", warm)
    monkeypatch.setattr(main, "dispose_db", dispose)
    async with main.lifespan(None):
        assert init.await_count == int(enabled)
        assert backfill.await_count == int(enabled)
        assert session.call_count == int(enabled)
        warm.assert_awaited_once()
    dispose.assert_awaited_once()
