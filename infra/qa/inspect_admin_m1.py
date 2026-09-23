"""Read-only, exact-account workflow diagnosis. No credentials or other accounts."""
import argparse
import asyncio
import json
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings


async def main(args):
    logging.disable(logging.CRITICAL)
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as db:
            subject = (await db.execute(text('SELECT profile_uuid FROM user_accounts WHERE username=:name'),
                                       {'name': args.account})).scalar_one_or_none()
            if not subject:
                print(json.dumps({'account_found': False})); return
            params = {'subject': subject}
            if args.conversation is None:
                rows = (await db.execute(text('''SELECT c.id,c.title,c.created_at,c.updated_at,
                    r.current_module,r.last_transition_reason,COUNT(m.id) AS messages,
                    SUM(m.role='assistant' AND COALESCE(m.routing_reasoning_content,'')='') AS empty_router_rows
                    FROM conversations c LEFT JOIN conversation_runtime_states r ON r.conversation_id=c.id
                    LEFT JOIN conversation_messages m ON m.conversation_id=c.id WHERE c.subject_id=:subject
                    GROUP BY c.id,c.title,c.created_at,c.updated_at,r.current_module,r.last_transition_reason
                    ORDER BY c.updated_at DESC LIMIT 12'''), params)).mappings().all()
                print(json.dumps({'account_found': True, 'conversations': [dict(r) for r in rows]},
                                 ensure_ascii=False, default=str)); return
            params['conversation'] = args.conversation
            session = (await db.execute(text('SELECT session_id FROM conversations WHERE id=:conversation AND subject_id=:subject'), params)).scalar_one_or_none()
            if not session:
                raise ValueError('Conversation not owned by requested account')
            params['session'] = session
            messages = (await db.execute(text('''SELECT id,position,role,content,model_name,router_model_name,
                CHAR_LENGTH(COALESCE(routing_reasoning_content,'')) AS router_chars,
                LEFT(routing_reasoning_content,180) AS router_summary,
                router_duration_ms,reasoning_tokens,main_generation_duration_ms,created_at
                FROM conversation_messages WHERE conversation_id=:conversation ORDER BY position,id LIMIT 180'''), params)).mappings().all()
            events = (await db.execute(text('''SELECT assistant_message_id,stage,model_name,duration_ms,
                reasoning_tokens,finish_reason,error_code,event_metadata,created_at FROM ai_execution_events
                WHERE subject_id=:subject AND session_id=:session AND stage IN ('module_router','clinical_extraction')
                ORDER BY id LIMIT 180'''), params)).mappings().all()
            decisions = (await db.execute(text('''SELECT turn_id,module_name,decision_type,decision_value,created_at
                FROM ai_decision_logs WHERE conversation_id=:conversation ORDER BY created_at LIMIT 180'''), params)).mappings().all()
            records = (await db.execute(text('''SELECT record_status,confirmation_status,ba_explanation_status,
                goal_setting_willingness,functional_chain_summary,event_experience,updated_at
                FROM module_one_record WHERE user_id=:subject ORDER BY updated_at DESC LIMIT 3'''),params)).mappings().all()
            state = (await db.execute(text('''SELECT status,completed_steps,completion_source,evidence_status,updated_at
                FROM user_module_one_state WHERE user_id=:subject'''),params)).mappings().all()
            result = {'conversation': args.conversation, 'messages':[dict(r) for r in messages],
                'events':[dict(r) for r in events], 'decisions':[dict(r) for r in decisions],
                'm1_records':[dict(r) for r in records], 'm1_state':[dict(r) for r in state]}
            if args.section != 'all':
                result = {key: value for key,value in result.items() if key in {'conversation', *args.section.split(',')}}
            print(json.dumps(result, ensure_ascii=False,default=str))
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--account', default='admin')
    parser.add_argument('--conversation', type=int)
    parser.add_argument('--section', default='all')
    asyncio.run(main(parser.parse_args()))
