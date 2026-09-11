"""Scoped deployment probe: read shared knowledge/prompts, no user records or writes."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.db import get_sessionmaker,dispose_db
from app.knowledge_mediator import mediate_knowledge
from app.prompt_store import PROMPT_DEFINITION_BY_KEY,effective_mediator_prompt
from app.providers import configured_providers,get_provider
from app.retrieval import get_knowledge_base
from app.test_workbench import EvaluationCase
from sqlalchemy import select,func

async def main(configuration_only):
    settings=get_settings()
    assert not settings.startup_db_maintenance, 'Production startup maintenance must stay disabled'
    assert 'knowledge_mediator' in PROMPT_DEFINITION_BY_KEY
    async with get_sessionmaker()() as db:
        count=await db.scalar(select(func.count()).select_from(EvaluationCase))
        prompt=await effective_mediator_prompt(db)
    result={'mediator_enabled':settings.knowledge_mediator_enabled,'providers':configured_providers(),'evaluation_cases':count,'prompt_available':bool(prompt)}
    if not configuration_only:
        query='身体活动和情绪的关系是什么，行为激活如何帮助改变？'
        chunks=await get_knowledge_base().search(module='module_1',query=query,top_k=3)
        assert chunks, 'No knowledge retrieved for smoke probe'
        _,_,metrics=await mediate_knowledge(state={'user_input':query},module='module_1',knowledge=chunks,
            provider=get_provider('deepseek'),settings=settings,prompt=prompt)
        result['retrieved_count']=len(chunks)
        result['mediator']=metrics
    print(json.dumps(result,ensure_ascii=False))
    await dispose_db()
    if not configuration_only and result['mediator']['status']!='completed':
        raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--configuration-only',action='store_true')
    asyncio.run(main(parser.parse_args().configuration_only))
