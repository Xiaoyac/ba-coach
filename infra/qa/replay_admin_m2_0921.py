"""Replay the owned failed transcript, without modifying production records."""
import asyncio
import json
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import get_settings
from app.models import Conversation, ConversationMessage, UserAccount
from app.providers import get_provider
from app.clinical_extraction import extract_module_record_detailed
from app.clinical_fields import MODULE_SPECS
from app.clinical_store import coerce
from app.goal_contract import proposal_evidence, recover_goal_proposal
from app.v2_workflow import record_values
from app.workflow_readiness import evaluate_readiness

async def main():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine)() as db:
            uid = await db.scalar(select(UserAccount.profile_uuid).where(UserAccount.username == 'admin'))
            messages = list((await db.execute(select(ConversationMessage).join(Conversation).where(
                Conversation.subject_id == uid, Conversation.id == 192,
                ConversationMessage.id <= 3038).order_by(ConversationMessage.position))).scalars())
        assert messages and messages[-1].id == 3038
        transcript = '\n'.join(m.role + '：' + m.content for m in messages)
        provider = get_provider('deepseek')
        raw, completion = await extract_module_record_detailed(provider, module='module_2',
            transcript=transcript, max_tokens=4800)
        data = coerce(MODULE_SPECS['module_2'], raw)
        for key in ('goal_proposal', 'plan_context', 'activity_observations', 'activity_corrections'):
            if isinstance(raw.get(key), (dict, list)):
                data[key] = raw[key]
        values = record_values('module_2', data)
        activity = values.get('activity_content') or ''
        recovered = recover_goal_proposal(messages, activity)
        result = {'messages': [dict(id=m.id, position=m.position, conversation_id=m.conversation_id,
            role=m.role, content=m.content) for m in messages], 'raw': raw, 'data': data,
            'readiness': evaluate_readiness('module_2', values),
            'proposal_evidence': proposal_evidence(raw.get('goal_proposal'), messages, activity),
            'recovered': recovered,
            'recovered_evidence': proposal_evidence(recovered, messages, activity),
            'finish_reason': completion.finish_reason}
        Path('/tmp/bacoach-m2-replay-0921.json').write_text(json.dumps(result, ensure_ascii=False, default=str), encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k != 'messages'}, ensure_ascii=False, default=str))
    finally:
        await engine.dispose()

asyncio.run(main())
