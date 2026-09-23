"""Owner-scoped diagnosis using the app's existing provider; NEVER persists.

Runs two extractions of the selected admin transcript on the same Doubao service
already used for those messages. By default emits validation statistics only,
no raw text, reasoning, clinical field values, identity or secrets. The optional
--probe-next-reply emits one generated diagnostic reply, never persists it.
Explicit --live-provider
is mandatory; CANDIDATE_SOURCES is supplied in memory by the SSH runner.
"""
import argparse
import asyncio
import json
import logging
import sys
import types
from time import perf_counter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from app.config import get_settings
from app.clinical_store import coerce
from app.providers.doubao import DoubaoProvider


async def main(args):
    if not args.live_provider:
        raise SystemExit('Explicit --live-provider required; uses existing model service')
    logging.disable(logging.CRITICAL)
    cfg = get_settings()
    engine = create_async_engine(cfg.database_url)
    try:
        async with engine.connect() as db:
            rows = (await db.execute(text('''SELECT m.id,m.role,m.content FROM conversation_messages m
                JOIN conversations c ON c.id=m.conversation_id
                JOIN user_accounts a ON a.profile_uuid=c.subject_id
                WHERE a.username=:account AND c.id=:conversation AND m.id<=:through
                ORDER BY m.position,m.id'''), {'account':'admin','conversation':args.conversation,
                                              'through':args.through})).mappings().all()
        assert rows and rows[-1]['id'] == args.through and rows[-1]['role'] == 'assistant'
        turns = [(r['role'], r['content']) for r in rows if r['content']]
        if args.with_synthetic_completion:
            turns += [
                ('assistant', '先补充BA的基本含义：情绪、精力和行动相互影响。活动和反馈减少可能让困扰持续，'
                 '这是一般模型，不代表你的经历一定如此。从能调整的行动入手，可能获得新的反馈。'
                 '行动不保证立刻开心。我们是尝试、观察、调整，不是要求意志力。你对这些还有什么疑问？'),
                ('user', '我明白了，行动和状态会互相影响，尝试后要观察反馈再调整，并不保证马上开心。'
                 '我没有其他疑问，我愿意开始目标设定。'),
                ('assistant', '收到你的理解和意愿。'),
            ]
        if args.probe_next_reply:
            from app.prompt_store import effective_prompt_pair
            async with AsyncSession(engine) as db:
                prompt_pair = await effective_prompt_pair(db, 'module_1')
    finally:
        await engine.dispose()
    for mode in (('after',) if args.candidate_only else ('before', 'after')):
        if mode == 'after':
            for name in ('clinical_fields', 'm1_contract', 'clinical_extraction'):
                module = types.ModuleType('app.' + name)
                module.__package__ = 'app'
                sys.modules[module.__name__] = module
                exec(compile(CANDIDATE_SOURCES[name], '<m1-replay-candidate>', 'exec'), module.__dict__)
        from app.clinical_extraction import extract_module_record_detailed
        from app.clinical_fields import MODULE_SPECS
        from app.m1_contract import snapshot
        provider = DoubaoProvider(cfg)
        provider._client = provider._client.with_options(max_retries=0, timeout=40)
        started = perf_counter()
        try:
            if mode == 'after':
                from app.m1_contract import indexed_transcript
                transcript = indexed_transcript(turns)
            else:
                transcript = '\n'.join(f'{role}：{content}' for role, content in turns)
            raw, completion = await asyncio.wait_for(extract_module_record_detailed(provider,
                module='module_1', transcript=transcript, max_tokens=cfg.extraction_max_tokens), timeout=45)
            contract = snapshot(raw, coerce(MODULE_SPECS['module_1'], raw), turns, 'replay-only')['m1_contract']
            print(json.dumps({'mode': mode, 'turns': len(turns),
                'synthetic_suffix_not_persisted': args.with_synthetic_completion,
                'seconds': round(perf_counter()-started,3),
                'missing_fields':contract['missing_fields'], 'completed_steps':contract['completed_steps'],
                'validation_issues':contract.get('validation_issues',[]),
                'verified_fact_keys': [k for k in ('trigger','feeling','behavior','consequence') if k in contract['evidence']],
                'education_missing_topics': contract.get('education_missing_topics'),
                'evidence_turns': {key:value.get('turn') for key,value in contract['evidence'].items()},
                'education_distinct_spans': len({value['quote'] for key,value in contract['evidence'].items()
                                                if key.startswith('education_')}),
                'core_questions_resolved': raw.get('m1_contract',{}).get('core_questions_resolved'),
                'goal_consent_expressed': contract.get('goal_consent_expressed'),
                'consent_turn':contract['evidence'].get('consent',{}).get('turn'),
                'finish':completion.finish_reason, 'usage':completion.usage}, ensure_ascii=False), flush=True)
            if mode == 'after' and args.probe_next_reply:
                module = types.ModuleType('app.prompts')
                module.__package__ = 'app'
                sys.modules[module.__name__] = module
                exec(compile(CANDIDATE_SOURCES['prompts'], '<m1-reply-candidate>', 'exec'), module.__dict__)
                from app.m1_contract import dialogue_status
                from app.schemas import Message
                system = module.build_system_segments('module_1', global_prompt=prompt_pair[0],
                    module_prompt=prompt_pair[1], module_steps={'module_1':contract['completed_steps']},
                    clinical_context=['M1当前契约状态：'+json.dumps(dialogue_status(contract), ensure_ascii=False)])
                # Synthetic next user turn, never saved as an actual admin message.
                try:
                    reply = await asyncio.wait_for(provider.complete(system='\n\n'.join(part.text for part in system),
                        messages=[Message(role=role,content=content) for role,content in turns]
                        + [Message(role='user',content='我们可以继续了吗？')]), timeout=45)
                    print(json.dumps({'mode':'synthetic_next_reply_not_persisted', 'reply':reply.text,
                        'finish':reply.finish_reason, 'usage':reply.usage}, ensure_ascii=False), flush=True)
                except Exception as exc:
                    print(json.dumps({'mode':'synthetic_next_reply_not_persisted',
                        'error_type':type(exc).__name__, 'passed':False}), flush=True)
        finally:
            await provider._client.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--conversation',type=int,required=True)
    parser.add_argument('--through',type=int,required=True)
    parser.add_argument('--live-provider',action='store_true')
    parser.add_argument('--candidate-only',action='store_true')
    parser.add_argument('--with-synthetic-completion',action='store_true',
                        help='Append clearly synthetic BA education/understanding/consent in memory only')
    parser.add_argument('--probe-next-reply',action='store_true',
                        help='Additional model call with synthetic next turn; not a full E2E run')
    asyncio.run(main(parser.parse_args()))
