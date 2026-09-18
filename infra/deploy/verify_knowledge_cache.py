"""Post-release verification: production knowledge READS, synthetic memory DB WRITES.

Run as root with the release's Python and PYTHONPATH. Never creates production
users/messages, imports production knowledge or prints corpus text/credentials.
"""
import asyncio
import json
import sys

from dotenv import load_dotenv

load_dotenv('/etc/bacoach/backend.env')
load_dotenv('/etc/bacoach/workbench-safety.env', override=True)

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.catalog_knowledge_base import CatalogDatabaseKnowledgeBase
from app.config import get_settings
from app.db import Base, get_engine
from app.knowledge_store import import_knowledge_source
from app.models import KnowledgeSourceRecord, KnowledgeChunkRecord
from app.providers.base import Completion
from app.retrieval import get_knowledge_base


class SyntheticProvider:
    name = 'synthetic'
    model = 'synthetic'

    def __init__(self):
        self.calls = 0

    async def route_detailed(self, **kwargs):
        self.calls += 1
        payload = json.loads(kwargs['user'])
        key, rows = ('section_ids', payload['sections']) if 'sections' in payload else ('selected_ids', payload['candidates'])
        return Completion(text=json.dumps({key: [rows[0]['id']]}), model=self.model, finish_reason='stop')


async def main():
    settings = get_settings()
    assert settings.startup_db_maintenance is False
    assert settings.knowledge_result_cache_enabled is True
    engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(
            sync, tables=[KnowledgeSourceRecord.__table__, KnowledgeChunkRecord.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def publish(version):
        async with factory() as db:
            await import_knowledge_source(db, name='synthetic-cache-verification', category='BA',
                markdown='# 行为激活原理\n先行动，再观察心情。' + version, updated_by='synthetic')
            await db.commit()

    await publish('version-one')
    workers = [CatalogDatabaseKnowledgeBase(SyntheticProvider(), lambda: factory) for _ in range(2)]
    args = dict(module='module_1', query='没劲时该怎么开始？', top_k=2,
                cache_scope=('synthetic-user', 'synthetic-conversation'))
    for worker in workers:
        first, cold = await worker.search_with_diagnostics(**args)
        second, warm = await worker.search_with_diagnostics(**args)
        assert first and first == second
        assert cold['cache'] == 'miss' and cold['model_calls'] == 2
        assert warm['cache'] == 'hit' and warm['model_calls'] == 0
        assert worker.provider.calls == 2
    await publish('version-two')
    for worker in workers:
        hits, trace = await worker.search_with_diagnostics(**args)
        assert trace['cache'] == 'miss' and 'version-two' in hits[0].text
        assert 'version-one' not in hits[0].text
    await engine.dispose()

    # Read-only production SQL compatibility / loaded release configuration.
    live = get_knowledge_base()
    count = await live.warmup()
    assert count > 0 and live._index_revision
    first_index = live._index
    assert await live._load_index() is first_index
    await get_engine().dispose()
    print(json.dumps({'python': sys.version.split()[0], 'retrieval_mode': live.ranking_mode,
        'cache_enabled': settings.knowledge_result_cache_enabled,
        'cache_ttl_seconds': settings.knowledge_result_cache_ttl_seconds,
        'production_chunks_read': count, 'synthetic_cold_model_calls': 2,
        'synthetic_warm_model_calls': 0, 'independent_worker_refresh': 'passed',
        'production_database_writes': 0, 'status': 'passed'}))


if __name__ == '__main__':
    asyncio.run(main())
