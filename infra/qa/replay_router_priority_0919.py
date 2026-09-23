"""Read the reported admin transcript, call the existing provider, commit ONLY in RAM.

Production access is SELECT-only. No transcript, credentials or clinical values
are printed or saved. Each replay uses the same turns; no synthetic user suffix.
"""
import asyncio
import json
import logging
from time import perf_counter
from sqlalchemy import text, insert, select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import get_settings
from app.clinical_extraction import extract_module_record_detailed
from app.clinical_fields import MODULE_SPECS
from app.clinical_store import coerce
from app.m1_contract import indexed_transcript, snapshot
from app.providers.deepseek import DeepSeekProvider
from app.router_agent import decide_target_module_with_reasoning
from app.prompt_store import effective_router_prompt
from app.v2_workflow import record_values, record_steps, runtime_for
from app.database_v2_schema import metadata
from app.models import Conversation, ConversationMessage


async def commit_in_memory(turns, data, decision):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            for model in (Conversation, ConversationMessage):
                await connection.run_sync(model.__table__.create)
        async with maker() as db:
            await db.execute(insert(metadata.tables['user_profile']), {'uuid': 'probe'})
            await db.execute(insert(Conversation), {'id': 1, 'session_id': 'probe', 'subject_id': 'probe'})
            await db.execute(insert(metadata.tables['conversation_runtime_states']),
                {'conversation_id': 1, 'current_module': 'module_1', 'memory': {}})
            await db.execute(insert(metadata.tables['user_module_one_state']),
                {'user_id': 'probe', 'completed_steps': [], 'status': 'in_progress'})
            await db.execute(insert(ConversationMessage), [dict(id=i+1, conversation_id=1, position=i,
                role=role, content=body) for i, (role, body) in enumerate(turns)])
            await db.execute(insert(metadata.tables['module_one_record']), dict(id='probe-m1', user_id='probe',
                version_no=1, **record_values('module_1', data)))
            diagnostics = {}
            result = await record_steps(db, session_id='probe', user_id='probe', module='module_1',
                requested_target=decision.target_module, steps=decision.completed_steps,
                assistant_message_id=len(turns), revoked_steps=decision.revoked_steps,
                revocation_evidence=decision.revocation_evidence, diagnostics=diagnostics)
            await db.commit()
            _, runtime = await runtime_for(db, 'probe')
            goal_count = (await db.execute(select(func.count()).select_from(metadata.tables['pa_goals']))).scalar_one()
            return {'applied_target': result[0], 'next_turn_module': runtime['current_module'],
                    'goals_created': goal_count, 'diagnostics': diagnostics}
    finally:
        await engine.dispose()


async def main():
    logging.disable(logging.CRITICAL)
    cfg = get_settings()
    production = create_async_engine(cfg.database_url)
    try:
        async with async_sessionmaker(production)() as db:
            rows = (await db.execute(text('''SELECT m.id,m.role,m.content FROM conversation_messages m
                JOIN conversations c ON c.id=m.conversation_id
                JOIN user_accounts a ON a.profile_uuid=c.subject_id
                WHERE a.username=:account AND c.id=:conversation AND m.id<=:through
                ORDER BY m.position,m.id'''), {'account': 'admin', 'conversation': 125, 'through': 1769})).mappings().all()
            prompt = await effective_router_prompt(db)
            await db.rollback()
    finally:
        await production.dispose()
    assert rows and rows[-1]['id'] == 1769 and rows[-1]['role'] == 'assistant'
    turns = [(r['role'], r['content']) for r in rows if r['content']]
    provider = DeepSeekProvider(cfg)
    try:
        for attempt in range(1, 4):
            started = perf_counter()
            raw, completion = await asyncio.wait_for(extract_module_record_detailed(provider,
                module='module_1', transcript=indexed_transcript(turns), max_tokens=cfg.extraction_max_tokens), 45)
            data = snapshot(raw, coerce(MODULE_SPECS['module_1'], raw), turns, 'probe')
            data['m1_contract']['assistant_message_id'] = len(turns)
            decision = await asyncio.wait_for(decide_target_module_with_reasoning(provider,
                current_module='module_1', user_input=turns[-2][1], ai_output=turns[-1][1],
                has_pa_card=False, conversation_context='\n'.join(role + '：' + body for role, body in turns),
                completed_steps=data['m1_contract']['completed_steps'], system_prompt=prompt,
                max_tokens=cfg.router_reasoning_max_tokens), 40)
            committed = await commit_in_memory(turns, data, decision)
            result = {'attempt': attempt, 'turns': len(turns), 'seconds': round(perf_counter()-started, 2),
                'extraction_finish': completion.finish_reason,
                'extraction_missing': data['m1_contract']['missing_fields'],
                'extraction_issues': data['m1_contract'].get('validation_issues'),
                'router_target': decision.target_module, 'router_error': decision.error_code,
                **committed, 'production_writes': False}
            result['passed'] = committed['next_turn_module'] == 'module_2' and committed['goals_created'] == 0
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if not result['passed']:
                raise AssertionError('Actual transcript replay failed; inspect the compact evidence above')
    finally:
        await provider._client.close()


if __name__ == '__main__':
    asyncio.run(main())
