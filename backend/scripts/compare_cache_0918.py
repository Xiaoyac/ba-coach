"""Offline, reproducible OLD POLICY vs NEW POLICY cache comparison.

Uses an in-memory synthetic corpus, fake clock and deterministic fake model.
No production credentials, DB reads, network calls or accuracy claims.
Run from backend: .venv/Scripts/python scripts/compare_cache_0918.py --output PATH
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import retrieval
from app.catalog_knowledge_base import CatalogDatabaseKnowledgeBase
from app.config import Settings
from app.db import Base
from app.knowledge_store import IGNORED_KNOWLEDGE_SOURCE_NAMES, import_knowledge_source
from app.models import KnowledgeChunkRecord, KnowledgeSourceRecord
from app.providers.base import Completion
from app.semantic_catalog import SemanticCatalog


class DeterministicModel:
    name = model = 'offline-synthetic'

    def __init__(self):
        self.calls = 0

    async def route_detailed(self, **kwargs):
        self.calls += 1
        data = json.loads(kwargs['user'])
        field, rows = ('section_ids', data['sections']) if 'sections' in data else ('selected_ids', data['candidates'])
        return Completion(text=json.dumps({field: [rows[0]['id']] if rows else []}),
                          model=self.model, finish_reason='stop')


class OldPolicyKnowledgeBase(CatalogDatabaseKnowledgeBase):
    """The 20260917 _load_index policy: unconditionally clear on 300s audit.

    Reuses today's ranking/model validation, which is unchanged. This is a
    policy replay, not a claim of running an untouched historical executable.
    """
    async def _load_index(self, *, force=False):
        async with self._index_lock:
            for _attempt in range(2):
                revision = await self._read_revision()
                if (not force and self._index is not None and revision == self._index_revision
                        and retrieval.monotonic() - self._index_loaded_at < 300):
                    return self._index
                self.invalidate()
                epoch = self._index_epoch
                async with self._sessionmaker_provider()() as db:
                    rows = (await db.execute(select(KnowledgeChunkRecord, KnowledgeSourceRecord)
                        .join(KnowledgeSourceRecord, KnowledgeSourceRecord.id == KnowledgeChunkRecord.source_id)
                        .where(KnowledgeSourceRecord.name.notin_(IGNORED_KNOWLEDGE_SOURCE_NAMES))
                        .order_by(KnowledgeChunkRecord.id))).all()
                confirmed = await self._read_revision()
                if revision != confirmed or epoch != self._index_epoch:
                    continue
                self._index = tuple(retrieval.index_chunk(id=row.id, source_id=source.id,
                    source_name=source.name, category=source.category, heading=row.heading, content=row.content)
                    for row, source in rows)
                self._index_revision = revision
                self._index_loaded_at = retrieval.monotonic()
                return self._index
            raise retrieval.KnowledgeRevisionChanged('synthetic change')

    async def _search_index(self, index, **kwargs):
        if self._catalog is None or self._catalog_index is not index:
            self._catalog = SemanticCatalog(index, cache_public=False, public_cache_counts=self._public_cache_counts)
            self._catalog_index = index
        return await super()._search_index(index, **kwargs)


SCENARIOS = {
    'audit_boundary': [(290, '没动力时怎么开始？', 'room-a', False), (310, '没动力时怎么开始？', 'room-a', False)],
    'repeat_after_8_minutes': [(0, '没动力时怎么开始？', 'room-a', False), (480, '没动力时怎么开始？', 'room-a', False)],
    'evolving_dialogue': [(0, '最近下班很累', 'room-a', False), (30, '没动力时怎么开始？\n最近下班很累', 'room-a', False),
        (60, '不想散步，膝盖痛\n没动力时怎么开始？', 'room-a', False), (90, '没动力时怎么开始？', 'room-b', False)],
    'knowledge_update': [(0, '没动力时怎么开始？', 'room-a', False), (10, '没动力时怎么开始？', 'room-a', True)],
}


async def evaluate(policy, events):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync,
            tables=[KnowledgeSourceRecord.__table__, KnowledgeChunkRecord.__table__]))
    async def publish(version):
        async with factory() as db:
            await import_knowledge_source(db, name='offline synthetic', category='BA',
                markdown=f'# 行动建议\n从小步骤行动开始，记录自己的感受。版本{version}', updated_by='offline')
            await db.commit()
    await publish(1)
    model, clock = DeterministicModel(), [0.0]
    settings = Settings(_env_file=None, knowledge_result_cache_enabled=True,
                        knowledge_result_cache_ttl_seconds=300 if policy == 'old' else 900,
                        knowledge_result_cache_empty_ttl_seconds=45)
    with patch('app.retrieval.get_settings', return_value=settings), patch('app.retrieval.monotonic', side_effect=lambda: clock[0]):
        cls = OldPolicyKnowledgeBase if policy == 'old' else CatalogDatabaseKnowledgeBase
        kb = cls(model, lambda: factory)
        kb._result_cache._clock = lambda: clock[0]
        await kb.warmup()
        outputs, traces = [], []
        for second, query, room, changed in events:
            clock[0] = second
            if changed:
                await publish(2)
            hits, trace = await kb.search_with_diagnostics(module='module_1', query=query, top_k=2,
                cache_scope=('offline-user', room, 'False'))
            outputs.append([(hit.id, hit.text, hit.source, hit.score, hit.score_type) for hit in hits])
            traces.append({key: trace.get(key) for key in ('cache', 'cache_reason', 'status', 'model_calls')})
        stats = kb.cache_monitoring_stats()
    await engine.dispose()
    return {'requests': stats['requests'], 'hits': stats['hits'], 'hit_rate': stats['hit_rate'],
        'retrieval_model_calls': model.calls, 'public_preparation': stats['public_preparation'],
        'traces': traces, 'monitoring': stats}, outputs


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    args = parser.parse_args()
    report = {'kind': 'synthetic_policy_replay', 'network_calls': 0, 'production_database_writes': 0,
        'note': 'Mechanism validation only; not real-user hit rate, latency or retrieval accuracy.', 'scenarios': {}}
    for name, events in SCENARIOS.items():
        old, before = await evaluate('old', events)
        new, after = await evaluate('new', events)
        assert before == after, f'evidence changed: {name}'
        if name == 'knowledge_update':
            assert all('版本2' in row[1] and '版本1' not in row[1] for row in after[-1])
        report['scenarios'][name] = {'old': old, 'new': new, 'identical_evidence': before == after}
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(encoded + '\n', encoding='utf-8')
    print(encoded)


if __name__ == '__main__':
    asyncio.run(main())
