"""Read-only production-runtime verification using synthetic inputs only."""
import argparse
import asyncio
import importlib.util
import json
import logging
import sys
from types import SimpleNamespace

sys.path.insert(0, '/opt/bacoach/current/backend')
from app import knowledge_mediator as baseline
from app.config import get_settings
from app.db import get_sessionmaker, dispose_db
from app.prompt_store import effective_mediator_prompt
from app.providers.base import Completion
from app.retrieval import KnowledgeChunk


async def main(args):
    spec = importlib.util.spec_from_file_location('app.mediator_candidate_verify', args.candidate)
    candidate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = candidate
    spec.loader.exec_module(candidate)
    class Slow:
        async def route_detailed(self, **kwargs):
            await asyncio.sleep(.1)
    selected, block, metrics = await candidate.mediate_knowledge(state={'user_input':'synthetic'},module='module_2',
        knowledge=[KnowledgeChunk('one','synthetic','test')],provider=Slow(),
        settings=SimpleNamespace(knowledge_mediator_enabled=True,knowledge_mediator_timeout_seconds=.01))
    assert metrics['reason']=='timeout' and not selected and not block
    print(json.dumps({'python':sys.version.split()[0],'real_wait_for_timeout':metrics['reason'],'approved':len(selected)}),flush=True)
    if not args.compare_live:
        return
    import openai
    settings=get_settings()
    assert settings.database_url.startswith('mysql+') and not settings.startup_db_maintenance
    async with get_sessionmaker()() as db:
        prompt=await effective_mediator_prompt(db)
    client=openai.AsyncOpenAI(api_key=settings.deepseek_api_key,base_url=settings.deepseek_base_url,max_retries=0)
    class Provider:
        async def route_detailed(self, *, system, user, max_tokens, include_reasoning, reasoning_effort=None):
            result=await client.chat.completions.create(model=settings.deepseek_router_model,max_tokens=max_tokens,
                messages=[{'role':'system','content':system},{'role':'user','content':user}],
                extra_body={'thinking':{'type':'enabled'}},
                **({'reasoning_effort':reasoning_effort} if reasoning_effort else {}))
            choice=result.choices[0]
            return Completion(text=choice.message.content or '',model=result.model,
                reasoning_content=getattr(choice.message,'reasoning_content',None) or '',finish_reason=choice.finish_reason)
    chunks=[KnowledgeChunk('one','活动安排可以从可行的小步骤开始，根据体力和偏好调整。','synthetic-ba'),
            KnowledgeChunk('two','执行前可以明确时间地点，并准备下雨时的室内替代活动。','synthetic-plan')]
    cases=[('short',{'user_input':'我想尝试每天散步十分钟，可以怎么开始？'}),
           ('history',{'user_input':'如果下雨就改成在室内走十分钟，这样可以吗？',
             'chat_history':[SimpleNamespace(role='user',content='我最近很少出门，想在周五开始散步。'),
                SimpleNamespace(role='assistant',content='可以先考虑时间、地点和一个可行的小步骤。'),
                SimpleNamespace(role='user',content='晚饭后在小区里走十分钟，但是担心下雨。'),
                SimpleNamespace(role='assistant',content='可以准备一个适合你的室内替代活动。')]})]
    try:
        for case,state in cases:
            for name,module,limit in [('old',baseline,12),('new',candidate,20)]:
                debug={}
                selected,_,metrics=await module.mediate_knowledge(state=state,module='module_2',knowledge=chunks,
                    provider=Provider(),settings=SimpleNamespace(knowledge_mediator_enabled=True,
                        knowledge_mediator_timeout_seconds=limit,knowledge_mediator_reasoning_effort='low'),
                    prompt=prompt,debug_output=debug)
                print(json.dumps({'case':case,'version':name,'status':metrics['status'],'reason':metrics['reason'],
                    'duration_ms':metrics['duration_ms'],'approved':len(selected),'reasoning_returned':bool(debug.get('reasoning_content')),
                    'reasoning_chars':len(debug.get('reasoning_content') or ''),'timeout_seconds':limit},ensure_ascii=False),flush=True)
    finally:
        await client.close()
        await dispose_db()


if __name__=='__main__':
    logging.disable(logging.CRITICAL)
    parser=argparse.ArgumentParser()
    parser.add_argument('--candidate',required=True)
    parser.add_argument('--compare-live',action='store_true')
    asyncio.run(main(parser.parse_args()))
