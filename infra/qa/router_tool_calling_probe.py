"""Read-only Router A/B probe. Uses synthetic fixtures and the effective Router prompt.

Run from the release backend with its environment and PYTHONPATH, never through
chat endpoints. No business mutations, tool execution, migrations or deployment.
"""
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import logging
from pathlib import Path
import random
from time import perf_counter

from app.config import get_settings
from app.db import get_sessionmaker, dispose_db
from app.generation_policy import auxiliary_output_budget, native_thinking_options, ark_kimi_effort
from app.knowledge_context import KNOWLEDGE_TASKS, valid_knowledge_task
from app.prompt_store import effective_router_prompt
from app.providers.base import Completion, ProviderError
from app.providers.deepseek import DeepSeekProvider
from app.providers.prompt_cache import system_messages
from app.providers.usage import openai_usage
from app.router_agent import decide_target_module_with_reasoning
from sqlalchemy import text

NAME = 'submit_routing_decision'
TOOL = {'type': 'function', 'function': {
    'name': NAME,
    'description': 'Submit exactly one reply-routing decision. This proposes a module and knowledge task only; it does not confirm a goal, execute an action, or write business state.',
    'strict': True,
    'parameters': {'type': 'object', 'properties': {
        'target_module': {'type': 'string', 'enum': ['1', '2', '3', '4']},
        'knowledge_task': {'type': 'string', 'enum': list(KNOWLEDGE_TASKS)},
    }, 'required': ['target_module', 'knowledge_task'], 'additionalProperties': False},
}}
TOOL_CONTRACT = '''# 本次输出通道
保留上文全部模块业务判断规则。上文要求的模块数字或 JSON 均通过 submit_routing_decision 的参数提交，替代文本输出。
必须且只能调用该工具一次；不输出普通回复。target_module 和 knowledge_task 的含义不变。工具仅提交路由建议，不代表业务确认或持久化成功。'''


def validate_tool_choice(choice):
    calls = choice.message.tool_calls or []
    if choice.finish_reason != 'tool_calls' or len(calls) != 1:
        raise ValueError('Expected exactly one complete native tool call')
    call = calls[0]
    if not call.id or call.type != 'function' or call.function.name != NAME:
        raise ValueError('Wrong tool or missing call ID')
    body = json.loads(call.function.arguments)
    if not isinstance(body, dict) or set(body) != {'target_module', 'knowledge_task'}:
        raise ValueError('Unexpected fields')
    if type(body['target_module']) is not str or body['target_module'] not in ('1','2','3','4'):
        raise ValueError('Invalid module')
    if type(body['knowledge_task']) is not str or body['knowledge_task'] not in KNOWLEDGE_TASKS:
        raise ValueError('Invalid knowledge task')
    return body


class RecordedProvider:
    def __init__(self, provider, arm):
        self.provider, self.arm, self.model = provider, arm, provider.model
        self.calls = []

    def record(self, completion, *, channel, arguments=None):
        self.calls.append({'channel': channel, 'finish_reason': completion.finish_reason,
            'model': completion.model, 'usage': completion.usage, 'request_id': completion.request_id,
            'text': completion.text, 'arguments': arguments,
            'reasoning_characters': len(completion.reasoning_content or '')})
        return completion

    async def route_with_reasoning(self, *, system, user, max_tokens=None, **kwargs):
        self.system_hash = hashlib.sha256(system.encode()).hexdigest()
        self.user_hash = hashlib.sha256(user.encode()).hexdigest()
        if self.arm == 'json':
            return self.record(await self.provider.route_with_reasoning(
                system=system, user=user, max_tokens=max_tokens, **kwargs), channel='json')
        cfg = self.provider._settings
        # Same effective model, effort, budget and timeout as the production Router.
        tool_system = system + '\n\n' + TOOL_CONTRACT
        self.tool_system_hash = hashlib.sha256(tool_system.encode()).hexdigest()
        body = native_thinking_options(cfg, 'deepseek', enabled=True, model=cfg.deepseek_router_model)
        body['reasoning_effort'] = ark_kimi_effort(cfg.module_router_reasoning_effort)
        response = await self.provider._client.chat.completions.create(
            model=cfg.deepseek_router_model,
            messages=[*system_messages(tool_system, settings=cfg, model=cfg.deepseek_router_model),
                      {'role':'user','content':user}],
            tools=[TOOL], tool_choice='required', parallel_tool_calls=False, temperature=0,
            max_tokens=auxiliary_output_budget(cfg,'deepseek',cfg.deepseek_router_model,
                max_tokens or cfg.router_reasoning_max_tokens), extra_body=body)
        choice = response.choices[0]
        completion = Completion(text='', model=response.model, finish_reason=choice.finish_reason,
            reasoning_content=getattr(choice.message, 'reasoning_content', '') or '',
            usage=openai_usage(response.usage), request_id=getattr(response, '_request_id', None))
        # Record billed usage even if the tool arguments fail validation.
        row = {'channel':'native_tool', 'model':completion.model, 'finish_reason':choice.finish_reason,
            'usage':completion.usage, 'request_id':completion.request_id,
            'text':choice.message.content, 'reasoning_characters':len(completion.reasoning_content),
            'calls':[{'id':c.id,'name':c.function.name,'arguments':c.function.arguments} for c in choice.message.tool_calls or []]}
        self.calls.append(row)
        try:
            arguments = validate_tool_choice(choice)
        except (TypeError, ValueError) as exc:
            row['validation_error'] = str(exc)
            raise ProviderError('Invalid native Router tool call') from exc
        row['arguments'] = arguments
        row['task_scope_valid'] = valid_knowledge_task(arguments['knowledge_task'], 'module_'+arguments['target_module'])
        # Reuse the existing Router interpretation, fallback and task scoping.
        # This conversion consumes native arguments only, never prose or thoughts.
        return Completion(text=json.dumps(arguments), model=completion.model, usage=completion.usage,
            reasoning_content=completion.reasoning_content, finish_reason=completion.finish_reason,
            request_id=completion.request_id)

    async def route_detailed(self, **kwargs):
        if self.arm != 'json':
            raise ProviderError('No text fallback for the native tool arm')
        return self.record(await self.provider.route_detailed(**kwargs), channel='json_recovery')


async def run(args):
    logging.disable(logging.CRITICAL)
    cfg = get_settings()
    assert cfg.deepseek_router_model == 'kimi-k3'
    assert cfg.module_router_reasoning_effort == 'provider_default', 'Recheck effort matching before running'
    async with get_sessionmaker()() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        prompt = await effective_router_prompt(db)
        await db.rollback()
    await dispose_db()
    cases = json.loads(args.fixtures.read_text())
    assert len(cases) <= 30 and 1 <= args.repeats <= 3
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'effective-router-prompt.txt').write_text(prompt)
    (args.output/'tool-schema.json').write_text(json.dumps(TOOL,ensure_ascii=False,indent=2)+'\n')
    metadata = {'release':str(Path.cwd().resolve().parent), 'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
        'fixture_sha256':hashlib.sha256(args.fixtures.read_bytes()).hexdigest(),
        'model':cfg.deepseek_router_model,'effort':'low','max_tokens':max(cfg.router_reasoning_max_tokens,2048),
        'timeout_seconds':cfg.background_model_timeout_seconds,'seed':args.seed,
        'cases':len(cases),'repeats':args.repeats,'concurrency':2,'production_writes':False,
        'gold_labels':'synthetic author-labelled regression cases, not real-user accuracy',
        'tool_choice':'required','parallel_tool_calls':False,'strict_requested':True,
        'profile':'member and default-admin auxiliary thinking override false', 'temperature':0,
        'thinking_override':False}
    (args.output/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    jobs = [(case, rep, arm) for rep in range(args.repeats) for case in cases for arm in ['json','tool']]
    random.Random(args.seed).shuffle(jobs)
    limiter = asyncio.Semaphore(2)
    results_path = args.output/'results.jsonl'
    if results_path.exists():
        raise RuntimeError('Refuse to overwrite prior paid results; use a new output directory')
    async def job(case, rep, arm):
        async with limiter:
            # Independent clients avoid shared per-arm call state; bounded concurrency.
            provider = DeepSeekProvider(cfg).with_thinking(False)
            recorded = RecordedProvider(provider, arm)
            started = perf_counter()
            result = {'case_id':case['id'],'repeat':rep,'arm':arm,
                'expected_module':case['expected_module'],'expected_task':case.get('expected_task'),
                'current_module':case['current_module']}
            try:
                decision = await asyncio.wait_for(decide_target_module_with_reasoning(recorded,
                    current_module=case['current_module'], user_input=case['user_input'],
                    has_pa_card=case.get('has_pa_card',False), conversation_context=case['history'],
                    system_prompt=prompt, max_tokens=cfg.router_reasoning_max_tokens,
                    business_state=case.get('business_state',{}), routing_mode='router_only'),
                    timeout=cfg.background_model_timeout_seconds)
                d=asdict(decision); d.pop('reasoning_content',None)
                result.update(decision=d, correct=decision.target_module==case['expected_module'],
                    task_correct=(decision.knowledge_task==case['expected_task']) if case.get('expected_task') else None,
                    valid=decision.error_code is None)
            except Exception as exc:
                result.update(error=type(exc).__name__,correct=False,valid=False)
            finally:
                result.update(duration_ms=round((perf_counter()-started)*1000,3),calls=recorded.calls,
                    base_system_hash=getattr(recorded,'system_hash',None),user_hash=getattr(recorded,'user_hash',None))
                await provider._client.close()
            with results_path.open('a') as file:file.write(json.dumps(result,ensure_ascii=False)+'\n')
            print(json.dumps({k:result.get(k) for k in ['case_id','repeat','arm','correct','valid','duration_ms','error']},ensure_ascii=False),flush=True)
    await asyncio.gather(*(job(*j) for j in jobs))
    print('COMPLETED: read-only Router A/B',flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repeats',type=int,default=2)
    parser.add_argument('--seed',type=int,default=1003)
    asyncio.run(run(parser.parse_args()))
