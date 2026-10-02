"""Reindex only unchanged curated sources; dry run unless --apply is provided.

A complete JSON snapshot of KB sources/chunks is saved before the transaction.
Administrator-edited or unknown sources are left untouched. No schema changes.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.db import get_sessionmaker, dispose_db
from app.models import KnowledgeSourceRecord, KnowledgeChunkRecord
from app.knowledge_store import import_knowledge_source, _CHUNKER_VERSION
from scripts.import_project_knowledge import DEFAULT_KNOWLEDGE_DIR, SOURCE_CATEGORIES, _read_source


async def run(*, apply=False, backup=None):
    try:
        async with get_sessionmaker()() as db:
            sources = (await db.scalars(select(KnowledgeSourceRecord).with_for_update())).all()
            planned = []
            for source in sources:
                path = DEFAULT_KNOWLEDGE_DIR / source.name
                if source.name not in SOURCE_CATEGORIES or not path.is_file():
                    print(json.dumps({'name': source.name, 'status': 'skipped_unknown_source'}, ensure_ascii=False))
                    continue
                text = _read_source(path).replace('\r\n', '\n').replace('\r', '\n').strip()
                old_hash = hashlib.sha256(text.encode()).hexdigest()
                new_hash = hashlib.sha256((_CHUNKER_VERSION + '\n' + text).encode()).hexdigest()
                if source.category != SOURCE_CATEGORIES[source.name] or source.content_hash not in {old_hash, new_hash}:
                    print(json.dumps({'name': source.name, 'status': 'skipped_admin_modified'}, ensure_ascii=False))
                    continue
                planned.append((source.name, source.category, text))
            if apply:
                if backup is None:
                    raise ValueError('--backup is required with --apply')
                snapshot = {}
                for model in (KnowledgeSourceRecord, KnowledgeChunkRecord):
                    snapshot[model.__tablename__] = [dict(r) for r in
                        (await db.execute(select(model.__table__))).mappings().all()]
                # Exclusive creation avoids silently overwriting the rollback snapshot.
                with Path(backup).open('x', encoding='utf-8') as stream:
                    import os
                    os.fchmod(stream.fileno(), 0o600)
                    json.dump(snapshot, stream, ensure_ascii=False, default=str)
                for name, category, markdown in planned:
                    result = await import_knowledge_source(db, name=name, category=category,
                        markdown=markdown, updated_by='hierarchy-reindex-20261002')
                    print(json.dumps({'name': name, 'chunks': result.chunk_count,
                        'status': 'unchanged' if result.unchanged else 'reindexed'}, ensure_ascii=False))
                await db.commit()
            print(json.dumps({'eligible': len(planned), 'apply': apply}))
    finally:
        await dispose_db()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply, backup=args.backup))
