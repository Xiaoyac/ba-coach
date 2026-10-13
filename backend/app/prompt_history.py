"""Versioned writes. Database triggers also capture ORM/scripts/direct SQL writes.

All public edits require the version the editor actually read. Restoration copies
exact historical text into a new override, never removes later revisions.
"""
from datetime import datetime, timezone
from difflib import SequenceMatcher
from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from .models import PromptOverride
from .prompt_history_models import PromptHistoryState, PromptRevision
from .prompt_store import PROMPT_DEFINITIONS, PROMPT_DEFINITION_BY_KEY


def now():
    return datetime.now(timezone.utc)


async def initialize_history(engine):
    """Idempotent install, run with writers stopped for the initial deployment."""
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: PromptHistoryState.__table__.create(c, checkfirst=True))
        await conn.run_sync(lambda c: PromptRevision.__table__.create(c, checkfirst=True))
    from sqlalchemy.ext.asyncio import async_sessionmaker
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        for definition in PROMPT_DEFINITIONS:
            state = await db.get(PromptHistoryState, definition.key)
            if state is not None:
                continue
            row = (await db.execute(select(PromptOverride).where(PromptOverride.prompt_key == definition.key))).scalar_one_or_none()
            db.add(PromptHistoryState(prompt_key=definition.key, revision=1, default_content=definition.default_content))
            db.add(PromptRevision(prompt_key=definition.key, revision=1,
                content=row.content if row else definition.default_content, is_overridden=row is not None,
                actor=row.updated_by if row else 'system:source-default', action='baseline',
                created_at=row.updated_at if row else now()))
        await db.commit()
    dialect = engine.dialect.name
    if dialect not in ('sqlite', 'mysql'):
        raise RuntimeError('Prompt audit triggers are required for this database dialect')
    async with engine.begin() as conn:
        for name, ddl in trigger_definitions(dialect):
            # Never drop audit triggers during routine startup or upgrades.
            if dialect == 'sqlite':
                exists = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger' AND name=:name"), {'name': name})).first()
            else:
                exists = (await conn.execute(text('SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME=:name'), {'name': name})).first()
            if not exists:
                await conn.execute(text(ddl))


def trigger_definitions(dialect):
    stamp = "strftime('%Y-%m-%d %H:%M:%f','now')" if dialect == 'sqlite' else 'UTC_TIMESTAMP(6)'
    for event in ('INSERT', 'UPDATE', 'DELETE'):
        ref = 'OLD' if event == 'DELETE' else 'NEW'
        key = f'{ref}.prompt_key'
        content = 'default_content' if event == 'DELETE' else 'NEW.content'
        actor = "'system:database'" if event == 'DELETE' else "COALESCE(NULLIF(NEW.updated_by, ''), 'system:database')"
        if event == 'UPDATE':
            actor = "CASE WHEN NEW.updated_by <> OLD.updated_by OR NEW.updated_by LIKE 'system:%' OR NEW.updated_by LIKE 'deployment:%' THEN NEW.updated_by ELSE 'system:database' END"
        action = 'reset_default' if event == 'DELETE' else 'save' 
        name = 'prompt_audit_' + event.lower()
        guard = (f"SELECT RAISE(ABORT, 'Initialize prompt history before writing') WHERE NOT EXISTS (SELECT 1 FROM prompt_history_state WHERE prompt_key={key});" if dialect == 'sqlite' else
                 f"IF NOT EXISTS (SELECT 1 FROM prompt_history_state WHERE prompt_key={key}) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Initialize prompt history before writing'; END IF;")
        if event == 'UPDATE':
            guard += ("SELECT RAISE(ABORT, 'Prompt keys are immutable') WHERE NEW.prompt_key <> OLD.prompt_key;" if dialect == 'sqlite' else "IF NEW.prompt_key <> OLD.prompt_key THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Prompt keys are immutable'; END IF;")
        yield name, f'''CREATE TRIGGER {name} AFTER {event} ON prompt_overrides {'FOR EACH ROW' if dialect == 'mysql' else ''}
BEGIN
 {guard}
 UPDATE prompt_history_state SET revision=revision+1 WHERE prompt_key={key};
 INSERT INTO prompt_revisions (prompt_key,revision,content,is_overridden,actor,action,created_at,restored_from)
 SELECT prompt_key,revision,{content},{0 if event == 'DELETE' else 1},COALESCE(pending_actor,{actor}),COALESCE(pending_action,'{action}'),{stamp},pending_restore
 FROM prompt_history_state WHERE prompt_key={key};
 UPDATE prompt_history_state SET pending_actor=NULL,pending_action=NULL,pending_restore=NULL WHERE prompt_key={key};
END'''
    for event in ('UPDATE', 'DELETE'):
        name = 'prompt_history_immutable_' + event.lower()
        rejection = "SELECT RAISE(ABORT, 'Prompt history is append-only');" if dialect == 'sqlite' else "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Prompt history is append-only';"
        yield name, f"CREATE TRIGGER {name} BEFORE {event} ON prompt_revisions {'FOR EACH ROW' if dialect == 'mysql' else ''} BEGIN {rejection} END"


async def sync_source_defaults(db: AsyncSession):
    """Audit a changed fallback on deployment, without replacing admin overrides."""
    for definition in PROMPT_DEFINITIONS:
        state = (await db.execute(select(PromptHistoryState).where(PromptHistoryState.prompt_key == definition.key).with_for_update())).scalar_one()
        if state.default_content == definition.default_content:
            continue
        row = (await db.execute(select(PromptOverride).where(PromptOverride.prompt_key == definition.key))).scalar_one_or_none()
        state.default_content = definition.default_content
        if row is None:
            state.revision += 1
            db.add(PromptRevision(prompt_key=definition.key, revision=state.revision,
                content=definition.default_content, is_overridden=False, actor='system:source-default',
                action='source_update', created_at=now()))
    await db.commit()


async def snapshot(db: AsyncSession, key: str):
    return (await db.execute(select(PromptRevision).where(PromptRevision.prompt_key == key).order_by(PromptRevision.revision.desc()).limit(1))).scalar_one()


async def change_prompt(db: AsyncSession, key: str, *, actor: str, base_revision: int,
                        content: str | None = None, restored_from: int | None = None):
    # A real UPDATE serializes absent overrides too, including SQLite (whose
    # SELECT FOR UPDATE is ignored). Triggers advance the same revision token.
    locked = await db.execute(update(PromptHistoryState).where(
        PromptHistoryState.prompt_key == key, PromptHistoryState.revision == base_revision
    ).values(pending_actor=actor, pending_action='restore' if restored_from else 'reset_default' if content is None else 'save',
             pending_restore=restored_from).execution_options(synchronize_session=False))
    if locked.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, '提示词已被其他管理员或系统更新。你的草稿已保留，请载入最新版本后重新比较。')
    row = (await db.execute(select(PromptOverride).where(PromptOverride.prompt_key == key).execution_options(populate_existing=True))).scalar_one_or_none()
    if content is None:
        if row is not None:
            await db.delete(row)
        else:
            await db.execute(update(PromptHistoryState).where(PromptHistoryState.prompt_key == key).values(pending_actor=None,pending_action=None,pending_restore=None))
    elif row is None:
        db.add(PromptOverride(prompt_key=key,content=content,updated_by=actor,updated_at=now()))
    else:
        row.content, row.updated_by, row.updated_at = content, actor, now()
    await db.flush()
    result = await snapshot(db, key)
    await db.commit()
    return result


def compare_text(before: str, after: str):
    """Line alignment with bounded character diffs, including Chinese text."""
    chunks = []
    def add(kind, value):
        if not value: return
        if chunks and chunks[-1]['kind'] == kind: chunks[-1]['text'] += value
        else: chunks.append({'kind':kind,'text':value})
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    for tag,a,b,c,d in SequenceMatcher(None,old,new).get_opcodes():
        left,right=''.join(old[a:b]),''.join(new[c:d])
        if tag == 'equal': add('equal',left)
        elif tag == 'replace' and len(left)*len(right) <= 1_000_000:
            for inner,i,j,k,l in SequenceMatcher(None,left,right,autojunk=False).get_opcodes():
                if inner == 'equal': add('equal',left[i:j])
                else:
                    add('delete',left[i:j]);add('insert',right[k:l])
        else:
            add('delete',left);add('insert',right)
    return chunks
