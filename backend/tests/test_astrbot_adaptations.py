"""Behavioral regressions for the adopted context and execution safeguards."""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.context_epochs import load_epoch_history
from app.config import get_settings
from app.history_compressor import HistoryCompressor
from app.knowledge_store import chunk_markdown, import_knowledge_source
from app.models import ConversationContextCheckpoint
from app.pa_tool_loop import PAToolReply
from app.providers.base import Completion, ProviderError, StreamDelta
from app.schemas import Message
from test_main_prefix_cache import epoch_db


@pytest.mark.parametrize('verdict', ['{"valid": false}', '{"valid": "true"}',
                                    'not-json', '[]'])
async def test_unverified_summary_never_replaces_history(epoch_db, verdict):
    db, rows = epoch_db
    provider = SimpleNamespace(name='stub', route_detailed=AsyncMock(return_value=
        Completion(text='用户以前有每天跑步的习惯。', model='stub', finish_reason='stop')),
        verify_summary=AsyncMock(return_value=Completion(text=verdict, model='auditor',
            request_id='audit-id', finish_reason='stop', usage={'input_tokens': 40})))
    result = await load_epoch_history(db, subject_id='owner', session_id='epoch-owned',
        user_message_id=rows[-1].id, provider=provider, token_budget=1000, retain_tokens=300)
    assert result.summary == '' and len(result.messages) == 100
    assert result.metrics['compaction_deferred']
    assert await db.get(ConversationContextCheckpoint, rows[0].conversation_id) is None
    audit = result.metrics['compaction_requests'][1]
    assert audit['kind'] == 'fidelity_audit' and not audit['accepted']
    assert audit['request_id'] == 'audit-id' and audit['usage']['input_tokens'] == 40
    source = json.loads(provider.verify_summary.call_args.kwargs['source'])
    assert all(m['id'] != rows[-1].id for m in source['messages'])


async def test_audited_summary_can_commit_and_legacy_checkpoint_invalidates(epoch_db):
    db, rows = epoch_db
    legacy_digest = hashlib.sha256(json.dumps([(r.id, r.role, r.content, str(r.created_at))
        for r in rows[:2]], ensure_ascii=False).encode()).hexdigest()
    db.add(ConversationContextCheckpoint(conversation_id=rows[0].conversation_id,
        through_message_id=rows[1].id, source_digest=legacy_digest, summary='旧的未核对摘要'))
    await db.commit()
    provider = SimpleNamespace(name='stub', route_detailed=AsyncMock(return_value=
        Completion(text='用户有运动意愿，尚未确认计划。', model='stub', finish_reason='stop')),
        verify_summary=AsyncMock(return_value=Completion(text='{"valid":true}',
            model='stub', finish_reason='stop')))
    result = await load_epoch_history(db, subject_id='owner', session_id='epoch-owned',
        user_message_id=rows[-1].id, provider=provider, token_budget=1000, retain_tokens=300)
    await db.commit()
    assert result.metrics['checkpoint_invalidated'] and result.metrics['epoch_compacted']
    source = json.loads(provider.route_detailed.call_args.kwargs['user'])
    assert source['previous_summary'] == '' and source['messages'][0]['id'] == rows[0].id
    assert (await db.get(ConversationContextCheckpoint, rows[0].conversation_id)).summary == result.summary


async def test_audit_timeout_without_headroom_is_explicit_failure(epoch_db):
    db, rows = epoch_db
    provider = SimpleNamespace(name='stub', route_detailed=AsyncMock(return_value=
        Completion(text='summary', model='stub', finish_reason='stop')),
        verify_summary=AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(ProviderError, match='no_headroom'):
        await load_epoch_history(db, subject_id='owner', session_id='epoch-owned',
            user_message_id=rows[-1].id, provider=provider, token_budget=1000,
            retain_tokens=300, input_token_budget=2500)
    assert await db.get(ConversationContextCheckpoint, rows[0].conversation_id) is None


async def test_fidelity_audit_separates_user_evidence_from_assistant_assumptions():
    compressor = HistoryCompressor(SimpleNamespace(name='stub'), get_settings())
    compressor.route_detailed = AsyncMock(return_value=Completion(text='{"valid":false}', model='stub'))
    source = json.dumps({'previous_summary': '过去的事实', 'messages': [
        {'id': 1, 'role': 'assistant', 'content': '你已有跑步习惯。'},
        {'id': 2, 'role': 'user', 'content': '有意愿，但是没跑过。'},
        {'id': 3, 'role': 'assistant', 'content': '你一直有跑步习惯。'}]})
    await compressor.verify_summary(source=source, summary='用户有跑步习惯。')
    sent = json.loads(compressor.route_detailed.call_args.kwargs['user'])
    assert sent['source']['user_statements'] == [
        {'id': 2, 'role': 'user', 'content': '有意愿，但是没跑过。'}]
    assert sent['source']['previous_summary'] == '过去的事实'


def test_markdown_sections_retain_ancestors_and_do_not_parse_code_as_headings():
    chunks = chunk_markdown('# 活动\n## 准备\n### 示例\n内容\n```text\n# 不是标题\n```\n'
                            '## 复盘\n回顾结果\n# 其他\n其他内容')
    assert [c.heading for c in chunks] == ['活动 > 准备 > 示例', '活动 > 复盘', '其他']
    assert '# 不是标题' in chunks[0].content
    assert '准备' not in chunks[1].heading


def test_long_heading_keeps_complete_context_but_fits_database_column():
    title = '标题' * 300
    chunk = chunk_markdown('# ' + title + '\n原文内容')[0]
    assert len(chunk.heading) == 512
    assert title in chunk.content and '原文内容' in chunk.content


async def test_tool_loop_stops_repeated_side_effects_and_forces_final_response():
    class Provider:
        requests = []
        async def stream_tools(self, **kwargs):
            self.requests.append(kwargs)
            if kwargs['tool_choice'] == 'none':
                yield StreamDelta(kind='content', text='操作未完成，已停止重复尝试。')
            else:
                arguments = '{"b":2,"a":1}' if len(self.requests) % 2 else '{ "a":1, "b":2 }'
                yield StreamDelta(kind='tool_calls', tool_calls=[{
                    'id': f'call-{len(self.requests)}', 'type': 'function',
                    'function': {'name': 'save_pa_card', 'arguments': arguments}}])
            yield StreamDelta(kind='usage', finish_reason='stop')
    class Executor:
        definitions = []; displays = []; trace = []
        execute = AsyncMock(return_value={'status': 'blocked', 'reason': 'state_changed'})
    provider = Provider(); executor = Executor(); telemetry = {}
    output = [d async for d in PAToolReply(provider, executor, max_rounds=8,
        telemetry=telemetry).stream(system='policy', messages=[Message(role='user', content='修改计划')])]
    assert executor.execute.await_count == 2
    assert len(provider.requests) == 4 and provider.requests[-1]['tool_choice'] == 'none'
    assert telemetry['pa_tools']['repetition_blocked']
    assert '停止' in ''.join(d.text for d in output if d.kind == 'content')
    history = provider.requests[-1]['messages']
    assert [m['tool_call_id'] for m in history if m['role'] == 'tool'] == ['call-1', 'call-2', 'call-3']


async def test_reindex_preserves_admin_changes_and_backs_up_before_replacement(epoch_db, tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from scripts import reindex_hierarchical_knowledge as migration
    from app.models import KnowledgeSourceRecord, KnowledgeChunkRecord
    from sqlalchemy import select
    db, _ = epoch_db
    from app.db import Base
    async with db.bind.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[
            KnowledgeSourceRecord.__table__, KnowledgeChunkRecord.__table__]))
    original = '# 一级\n## 二级\n正确资料'
    for name in ('unchanged.md', 'edited.md'):
        (tmp_path / name).write_text(original)
        await import_knowledge_source(db, name=name, category='BA', markdown=original,
            updated_by='fixture')
    rows = (await db.scalars(select(KnowledgeSourceRecord))).all()
    by_name = {r.name: r for r in rows}
    by_name['unchanged.md'].content_hash = hashlib.sha256(original.encode()).hexdigest()
    by_name['edited.md'].content_hash = 'admin-edited-hash'
    await db.commit()
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(migration, 'get_sessionmaker', lambda: session)
    monkeypatch.setattr(migration, 'dispose_db', AsyncMock())
    monkeypatch.setattr(migration, 'DEFAULT_KNOWLEDGE_DIR', tmp_path)
    monkeypatch.setattr(migration, 'SOURCE_CATEGORIES', {'unchanged.md':'BA', 'edited.md':'BA'})
    await migration.run()
    assert by_name['unchanged.md'].updated_by == 'fixture'
    backup = tmp_path / 'backup.json'
    await migration.run(apply=True, backup=backup)
    assert by_name['unchanged.md'].updated_by == 'hierarchy-reindex-20261002'
    assert by_name['edited.md'].content_hash == 'admin-edited-hash'
    snapshot = json.loads(backup.read_text())
    assert len(snapshot['knowledge_sources']) == 2
    assert len(snapshot['knowledge_chunks']) == 2
    assert backup.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        await migration.run(apply=True, backup=backup)


@pytest.mark.parametrize('length', [64, 32])
def test_checkpoint_migration_allows_inherited_mysql_collation_but_checks_length(monkeypatch, length):
    from sqlalchemy.dialects import mysql
    from scripts import create_context_checkpoints as migration
    table = ConversationContextCheckpoint.__table__
    columns = [{'name': c.name, 'nullable': c.nullable, 'type': c.type} for c in table.columns]
    columns[2]['type'] = mysql.VARCHAR(length=length, collation='utf8mb4_general_ci')
    columns[3]['type'] = mysql.TEXT(collation='utf8mb4_general_ci')
    inspector = SimpleNamespace(has_table=lambda name: True,
        get_columns=lambda name: columns,
        get_pk_constraint=lambda name: {'constrained_columns': ['conversation_id']},
        get_foreign_keys=lambda name: [{'constrained_columns': ['conversation_id'],
            'referred_table':'conversations', 'referred_columns':['id'], 'options':{'ondelete':'CASCADE'}}])
    monkeypatch.setattr(migration, 'inspect', lambda connection: inspector)
    connection = SimpleNamespace(dialect=mysql.dialect())
    if length == 64:
        assert migration._plan(connection) == []
    else:
        with pytest.raises(RuntimeError, match='source_digest'):
            migration._plan(connection)
