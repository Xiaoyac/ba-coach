"""Synthetic model check; no user rows or mutations. Run with configured providers.

The candidate module can be loaded as app.provisional_reply_candidate by the
read-only remote harness. Outputs contain synthetic dialogue and usage only.
"""
import asyncio
import json
import os
import sys
from time import perf_counter

from app.config import get_settings
from app.context_pipeline import prepare_context
from app.db import get_sessionmaker
from app.prompt_store import effective_prompt_pair, effective_reply_lead_prompt
from app.providers import get_provider
from app.prompts import build_system_segments
from app.schemas import Message

memory = sys.modules.get('app.provisional_reply_candidate')
if memory is None:
    from app import provisional_reply as memory

CASES = [
    ('review', 'module_4', [('user', '昨天本来想散步十分钟，最后没出门。'),
      ('assistant', '当时发生了什么，让你没能出门？')], '加班太累了，有点失落，但我想换个更小的活动试试。'),
    ('boundary', 'module_1', [('user', '最近回家只想躺着，什么都不想做。'),
      ('assistant', '这种状态最近对你的生活有什么影响？')], '先别连续追问，我想先自己说说。'),
    ('correction', 'module_2', [('user', '我以前喜欢晚上跑步。'),
      ('assistant', '可以从你熟悉的活动开始讨论。')], '不过现在膝盖不舒服，别安排跑步，我想改成听音乐。'),
    ('explanation', 'module_1', [('user', '心情不好就不想出门。')], '为什么你建议从很小的活动开始？'),
]

async def main():
    settings = get_settings()
    if os.environ.get('LEAD_PROBE_ENV'):
        from dotenv import dotenv_values
        prior = dotenv_values(os.environ['LEAD_PROBE_ENV'])
        settings = settings.model_copy(update={
            'reply_lead_base_url': prior['DEEPSEEK_BASE_URL'],
            'reply_lead_api_key': prior['DEEPSEEK_API_KEY'],
            'reply_lead_model': 'qwen3.8-flash',
        })
    main_provider = get_provider('deepseek').with_deep_reply('low')
    async with get_sessionmaker()() as db:
        lead_prompt = await effective_reply_lead_prompt(db)
        prompts = {module: await effective_prompt_pair(db, module) for module in {c[1] for c in CASES}}
    for label, module, turns, user in CASES:
        history = [Message(role=r, content=c) for r,c in turns]
        start=perf_counter()
        system = build_system_segments(module,global_prompt=prompts[module][0],module_prompt=prompts[module][1],
            clinical_context=['合成验收场景：尚未确认新活动计划，没有执行任何保存操作。'],history=history)
        prepared = prepare_context(system=system,history=history,user_input=user,max_history_messages=80)
        from app.providers.base import as_text
        lead = await memory.generate_reply_lead(main_provider, user_input=user, history=history,
                                                settings=settings, prompt=lead_prompt, policy=as_text(prepared.system))
        print(json.dumps({'case':label,'phase':'lead', **{k:v for k,v in lead.items() if k!='context_pipeline'}},ensure_ascii=False),flush=True)
        if not lead['text']:
            continue
        system = memory.parallel_reply_system(prepared.system, lead['text'])
        text=''; raw_text=''; first=None; usage={}; finish=None
        prefix = memory.ContinuationPrefix(lead['text'])
        async for delta in main_provider.stream(system=system,messages=prepared.messages):
            if delta.kind=='content' and delta.text:
                if first is None: first=round((perf_counter()-start)*1000)
                raw_text+=delta.text
                text+=prefix.push(delta.text)
            if delta.usage: usage=delta.usage
            if delta.finish_reason: finish=delta.finish_reason
        text += prefix.finish()
        print(json.dumps({'case':label,'phase':'body','text':text,'first_body_ms':first,
            'duration_ms':round((perf_counter()-start)*1000),'usage':usage,'finish_reason':finish,
            'raw_text': raw_text, 'repeated_lead_removed': prefix.removed,
            'main_model':main_provider.model,'main_policy':main_provider.deep_reply_policy()},ensure_ascii=False),flush=True)
    from app.db import get_engine
    await get_engine().dispose()

if __name__=='__main__': asyncio.run(main())
