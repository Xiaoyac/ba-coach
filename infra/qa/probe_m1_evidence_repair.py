"""Six synthetic provider extractions, no DB or real account transcript.

Runner injects CANDIDATE_SOURCES; candidate code executes only in this process.
"""
import asyncio
import json
import logging
import sys
import types
from time import perf_counter
from app.config import get_settings
from app.clinical_store import coerce
from app.providers.doubao import DoubaoProvider


def install(name, source):
    name = 'app.' + name
    module = types.ModuleType(name)
    module.__package__ = 'app'
    sys.modules[name] = module
    exec(compile(source, '<synthetic-m1-candidate>', 'exec'), module.__dict__)


TURNS = [
    ('assistant', '你好，你最近有什么具体困扰？'),
    ('user', '最近下班之后总觉得焦虑，想学习却开始不了。'),
    ('assistant', '能说说最近一次当时发生了什么吗？'),
    ('user', '昨天晚上回到家，看到桌上的学习材料就很烦躁。'),
    ('assistant', '那时候你做了些什么？'),
    ('user', '打一会游戏，刷一会视频，没有去看材料。'),
    ('assistant', '这样做之后心情和事情有什么变化吗？'),
    ('user', '没有缓解，后来更烦了，学习也没开始。我觉得只是逃避了一下。'),
    ('assistant', '我整理一下：昨晚回家看到学习材料时你很烦躁，于是打游戏、刷视频而没有学习；之后没有缓解，反而更烦躁，学习也没有开始。这个总结符合吗？'),
    ('user', '对的，这就是我当时的情况。'),
    ('assistant', '之前尝试过什么缓解方法吗？'),
    ('user', '我试过先列小清单，但那天没感觉有帮助。'),
    ('assistant', '情绪、精力和行动相互影响。活动及反馈减少可能让困扰持续，这是一种一般解释，不代表你一定如此。'
     '从可控的行动入手可能带来新的反馈。行动不保证立刻开心。我们可以尝试、观察、再调整，而不是要求意志力。你对此有什么疑问吗？'),
    ('user', '我理解了，不是非得开心才能动，也不是做了就一定开心，而是观察之后再调整。我没有疑问。'),
    ('assistant', '你愿意进入接下来的目标设定环节吗？'),
    ('user', '愿意'), ('assistant', '收到你的意愿。'),
]


async def main():
    logging.disable(logging.CRITICAL)
    cfg = get_settings()
    cases = [('consented', TURNS),
             ('continued', TURNS + [('user', '我们可以继续了吗'), ('assistant', '收到，我们按前面聊到的继续。')]),
             ('withdrawn', TURNS + [('user', '我改主意了，先不要开始目标设定。'), ('assistant', '好的，尊重你的选择。')])]
    for mode in ('before', 'after'):
        if mode == 'after':
            for name in ('clinical_fields', 'm1_contract', 'clinical_extraction'):
                install(name, CANDIDATE_SOURCES[name])
        from app.clinical_extraction import extract_module_record_detailed
        from app.clinical_fields import MODULE_SPECS
        from app.m1_contract import snapshot
        for case, turns in cases:
            provider = DoubaoProvider(cfg)
            provider._client = provider._client.with_options(max_retries=0, timeout=35)
            started = perf_counter()
            try:
                if mode == 'after':
                    from app.m1_contract import indexed_transcript
                    transcript = indexed_transcript(turns)
                else:
                    transcript = '\n'.join(f'{role}：{content}' for role, content in turns)
                raw, completion = await asyncio.wait_for(extract_module_record_detailed(provider,
                    module='module_1', transcript=transcript, max_tokens=cfg.extraction_max_tokens), timeout=40)
                contract = snapshot(raw, coerce(MODULE_SPECS['module_1'], raw), turns, 'synthetic')['m1_contract']
                expected_ready = case != 'withdrawn'
                print(json.dumps({'mode': mode, 'case': case, 'seconds': round(perf_counter()-started, 3),
                    'model': completion.model, 'finish': completion.finish_reason,
                    'missing_fields': contract['missing_fields'],
                    'validation_issues': contract.get('validation_issues', []),
                    'consent_turn': contract['evidence'].get('consent', {}).get('turn'),
                    'passed': (not contract['missing_fields']) == expected_ready,
                    'usage': completion.usage}, ensure_ascii=False), flush=True)
            except Exception as exc:
                print(json.dumps({'mode': mode, 'case': case, 'error_type': type(exc).__name__}), flush=True)
            finally:
                await provider._client.close()


if __name__ == '__main__':
    asyncio.run(main())
