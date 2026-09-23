"""Bounded live-provider comparison with synthetic messages only; no DB access.

The runner supplies CANDIDATE_SOURCES before executing this script over SSH.
Production modules remain untouched. Eighteen requests, at most two concurrent.
"""
import asyncio
import json
import logging
import sys
import types
from time import perf_counter
from app.config import get_settings
from app.prompts import build_system_segments
from app.providers.deepseek import DeepSeekProvider
from app.providers.doubao import DoubaoProvider
from app.schemas import Message
from app.router_agent import decide_target_module_with_reasoning, ROUTER_AGENT_PROMPT, ROUTER_RUNTIME_CONTRACT
from app.workflow_contract import MODULE_STEP_KEYS


def load_candidate(module, source):
    name = 'app.generation_policy' if module == 'generation_policy' else f'app.providers._latency_{module}'
    loaded = types.ModuleType(name)
    loaded.__package__ = name.rpartition('.')[0]
    sys.modules[name] = loaded
    exec(compile(source, '<latency-candidate>', 'exec'), loaded.__dict__)
    return loaded


async def main():
    logging.disable(logging.CRITICAL)
    cfg = get_settings()
    candidate_cfg = cfg.model_copy(update={'chat_fast_ack_enabled': True,
        'deepseek_reasoning_effort': 'provider_default', 'module_router_reasoning_effort': 'provider_default'})
    # Experimental only, NOT the accepted rollout default: the initial probe
    # found an empty JSON completion at low effort. Keep that evidence visible.
    experimental_router_cfg = candidate_cfg.model_copy(update={'module_router_reasoning_effort': 'low'})
    load_candidate('generation_policy', CANDIDATE_SOURCES['generation_policy'])
    new_deepseek = load_candidate('deepseek', CANDIDATE_SOURCES['deepseek']).DeepSeekProvider
    new_doubao = load_candidate('doubao', CANDIDATE_SOURCES['doubao']).DoubaoProvider
    scenarios = [
        ('m1_ack', 'module_1', '最近下班后没精力，躺着刷手机，越刷越烦。',
         '我理解的是，下班疲惫时你会刷手机，之后烦躁增加。这份总结符合你的情况吗？', '好的'),
        ('m2_ack', 'module_2', '我想每周五天，晚饭后在小区散步15分钟，下雨就在室内走，想恢复一些精力。',
         '按你选择的活动，每周五天晚饭后在小区散步15分钟；下雨时室内走。你愿意按这个安排试试吗？', '好的'),
        ('m4_substantive', 'module_4', '我的目标是晚饭后散步15分钟。',
         '今天实际执行得怎么样？', '今天没有去散步，下班后太累了，先躺着休息，后来有点自责。'),
    ]
    semaphore = asyncio.Semaphore(2)
    async def one(provider_name, old_cls, new_cls, scenario):
        ident, module, user, previous, latest = scenario
        messages = [Message(role='user',content=user), Message(role='assistant',content=previous), Message(role='user',content=latest)]
        system = build_system_segments(module, clinical_context=[
            '这是合成验证，不是真实用户；当前系统 plan_confirmed=false，不得声称已保存、已切换模块。'])
        rows = []
        # Alternate order between scenarios to reduce a consistent warm-up bias.
        modes = [('before',old_cls,cfg),('after',new_cls,candidate_cfg)]
        if ident == 'm2_ack': modes.reverse()
        async with semaphore:
            for mode, cls, settings in modes:
                provider = cls(settings)
                provider._client = provider._client.with_options(max_retries=0, timeout=40)
                started = perf_counter()
                try:
                    result = await asyncio.wait_for(provider.complete(system=system,messages=messages),timeout=45)
                    row = {'provider':provider_name,'scenario':ident,'mode':mode,'seconds':round(perf_counter()-started,3),
                        'model':result.model,'usage':result.usage,'finish':result.finish_reason,
                        'reply':result.text, 'reasoning_chars':len(result.reasoning_content)}
                except Exception as exc:
                    row = {'provider':provider_name,'scenario':ident,'mode':mode,
                           'seconds':round(perf_counter()-started,3),'error_type':type(exc).__name__}
                finally:
                    await provider._client.close()
                rows.append(row)
                print(json.dumps(row,ensure_ascii=False),flush=True)
        return rows
    await asyncio.gather(*(one(name,old,new,s) for name,old,new in (
        ('deepseek',DeepSeekProvider,new_deepseek),('doubao',DoubaoProvider,new_doubao)) for s in scenarios))

    # Both incomplete holds and a positive transition: a router that always
    # stays in place must not pass merely because it is faster.
    router_cases = [
        ('m1_summary_not_education', 'module_1', False, [],
         '好的', '谢谢你确认这份关系总结，接下来可以了解行为激活。',
         '助手：下班疲惫时刷手机，之后烦躁增加。这份总结符合你的情况吗？', 'module_1'),
        ('m2_confirm_complete_card', 'module_2', True, list(MODULE_STEP_KEYS['module_2'][:-1]),
         '我确认就按这份完整计划执行。', '我们核对的计划没有变化。',
         '用户已理解PA并选择散步，希望恢复精力。助手核对：当前PA目标，活动内容散步，'
         '时间每天晚饭后七点，地点小区，时长15分钟，频率每周五天，潜在障碍下雨，应对方案室内走。你确认吗？', 'module_3'),
        ('m3_agreement_not_execution', 'module_3', True, list(MODULE_STEP_KEYS['module_3']),
         '好的，我明天开始，今天还没执行。', '了解了，今天还没有执行反馈。',
         '已说明如何记录今日活动；用户已同意明天执行计划和记录，但尚未开始活动。', 'module_3'),
    ]
    async def router_one(case):
        ident, current, has_card, steps, latest, reply, history, expected = case
        async with semaphore:
            for mode, cls, settings in [('before', DeepSeekProvider, cfg), ('experimental_low', new_deepseek, experimental_router_cfg)]:
                provider = cls(settings)
                provider._client = provider._client.with_options(max_retries=0, timeout=40)
                started = perf_counter()
                try:
                    decision = await asyncio.wait_for(decide_target_module_with_reasoning(provider,
                        current_module=current, user_input=latest, ai_output=reply, has_pa_card=has_card,
                        conversation_context=history, completed_steps=steps,
                        system_prompt=ROUTER_AGENT_PROMPT + '\n' + ROUTER_RUNTIME_CONTRACT,
                        max_tokens=cfg.router_reasoning_max_tokens), timeout=45)
                    row = {'stage': 'router', 'scenario': ident, 'mode': mode,
                        'seconds': round(perf_counter()-started, 3), 'model': decision.model,
                        'usage': decision.usage, 'finish': decision.finish_reason,
                        'target': decision.target_module, 'expected': expected,
                        'passed': decision.target_module == expected and not decision.error_code,
                        'error_code': decision.error_code}
                except Exception as exc:
                    row = {'stage': 'router', 'scenario': ident, 'mode': mode,
                        'seconds': round(perf_counter()-started, 3), 'error_type': type(exc).__name__}
                finally:
                    await provider._client.close()
                print(json.dumps(row, ensure_ascii=False), flush=True)
    await asyncio.gather(*(router_one(case) for case in router_cases))


if __name__ == '__main__':
    asyncio.run(main())
