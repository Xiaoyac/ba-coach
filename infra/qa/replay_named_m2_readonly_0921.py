"""Replay a named M2 transcript with the configured extractor; never persists."""
import argparse, asyncio, json
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

async def main(name, through=None):
    engine=create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine)() as db:
            uid=await db.scalar(select(UserAccount.profile_uuid).where(UserAccount.username==name))
            conv=(await db.execute(select(Conversation).where(Conversation.subject_id==uid).order_by(Conversation.updated_at.desc()))).scalars().first()
            statement=select(ConversationMessage).where(ConversationMessage.conversation_id==conv.id)
            if through is not None: statement=statement.where(ConversationMessage.id<=through)
            messages=list((await db.execute(statement.order_by(ConversationMessage.position))).scalars())
        transcript='\n'.join(m.role+'：'+m.content for m in messages if m.content)
        provider=get_provider('deepseek')
        raw,completion=await extract_module_record_detailed(provider,module='module_2',transcript=transcript,max_tokens=4800)
        data=coerce(MODULE_SPECS['module_2'],raw)
        for key in ('goal_proposal','plan_context','activity_observations','activity_corrections'):
            if isinstance(raw.get(key),(dict,list)): data[key]=raw[key]
        values=record_values('module_2',data); activity=values.get('activity_content') or ''
        recovered=recover_goal_proposal(messages,activity)
        result={'conversation':conv.id,'last_assistant':messages[-1].id,'activity':activity,
            'proposal':raw.get('goal_proposal'),'plan_fields':{k:values.get(k) for k in ('activity_content','schedule_text','location','duration_minutes','frequency_rule','potential_barriers','barrier_coping_plan')},
            'readiness':evaluate_readiness('module_2',values),
            'proposal_evidence':proposal_evidence(raw.get('goal_proposal'),messages,activity),
            'recovered':recovered,'recovered_evidence':proposal_evidence(recovered,messages,activity),
            'finish':completion.finish_reason}
        print(json.dumps(result,ensure_ascii=False,default=str))
    finally: await engine.dispose()

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--name',required=True); p.add_argument('--through',type=int); a=p.parse_args(); asyncio.run(main(a.name,a.through))
