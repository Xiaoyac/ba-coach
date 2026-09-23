"""Bounded M1 release probes: synthetic input only, never open a database."""
import asyncio
import json
import sys
from time import perf_counter
from unittest.mock import AsyncMock

from app.config import get_settings
from app.clinical_extraction import extract_module_record_detailed
from app.clinical_fields import MODULE_SPECS
from app.clinical_store import coerce
from app.m1_contract import consent_is_current, indexed_transcript, snapshot
from app.prompts import build_system_prompt, build_system_segments, M1_RUNTIME_CONTRACT
from app.providers.base import Completion
from app.providers.doubao import DoubaoProvider
from app.router_agent import decide_target_module_with_reasoning, format_routing_reasoning
from app.workflow_contract import MODULE_STEP_KEYS

TURNS = [
    ('assistant', '你最近有什么具体困扰？'),
    ('user', '昨天晚上回家，看到学习材料就烦躁。我打游戏、刷视频，没有学习；后来更烦躁，学习也没有开始。'),
    ('assistant', '昨晚看到学习材料时你很烦躁，于是打游戏、刷视频而没有学习；之后更烦躁，学习也没开始。这个总结符合吗？'),
    ('user', '对，这就是我当时的情况。'),
    ('assistant', '之前尝试过什么缓解方法？'),
    ('user', '我试过先列小清单，但那天没感觉有帮助。'),
    ('assistant', '情绪、精力和行动相互影响。活动及反馈减少可能让困扰持续，这是一种一般解释，不代表你一定如此。'
     '从可控的行动入手可能带来新的反馈。行动不保证立刻开心。我们可以尝试、观察、再调整，而不是要求意志力。你对此有什么疑问吗？'),
    ('user', '我理解了，不是非得开心才能动，也不是做了就一定开心，而是观察之后再调整。我没有疑问。'),
    ('assistant', '你愿意进入接下来的目标设定环节吗？'),
    ('user', '愿意'),
    ('assistant', '收到你的意愿。'),
    ('user', '我们可以继续了吗'),
    ('assistant', '我们会根据已经讨论过的内容继续。'),
]


async def synthetic():
    assert consent_is_current(TURNS, 9)
    assert not consent_is_current(TURNS + [('user', '先不要开始目标设定。')], 9)
    assert not consent_is_current([('assistant', '这个总结符合吗？'), ('user', '好的')], 1)
    for build in (build_system_prompt, build_system_segments):
        value = build('module_1', global_prompt='synthetic override', module_prompt='synthetic module')
        text = value if isinstance(value, str) else '\n'.join(segment.text for segment in value)
        assert M1_RUNTIME_CONTRACT in text and '只确认一次' in text
    provider = AsyncMock()
    provider.route_with_reasoning.return_value = Completion(text='', model='synthetic',
        finish_reason='length', reasoning_content='synthetic trace')
    provider.route_detailed.return_value = Completion(text=json.dumps({
        'target_module': '2', 'completed_steps': list(MODULE_STEP_KEYS['module_1'])}),
        model='synthetic', finish_reason='stop')
    decision = await decide_target_module_with_reasoning(provider, current_module='module_1',
        user_input='愿意', ai_output='synthetic', has_pa_card=False)
    assert decision.target_module == 'module_2' and decision.json_recovery['status'] == 'recovered'
    assert 'synthetic trace' in format_routing_reasoning(decision, 'module_1', 'module_1')
    assert provider.route_detailed.await_count == 1
    print(json.dumps({'synthetic_checks': 'PASS', 'database_operations': 0}), flush=True)


async def live():
    settings = get_settings()
    for name, turns, ready in [
        ('historical_consent_then_neutral_turn', TURNS, True),
        ('withdrawal', TURNS + [('user', '我改主意了，先不要开始目标设定。'),
                              ('assistant', '好的，尊重你的选择。')], False),
    ]:
        provider = DoubaoProvider(settings)
        started = perf_counter()
        try:
            raw, completion = await asyncio.wait_for(extract_module_record_detailed(provider,
                module='module_1', transcript=indexed_transcript(turns),
                max_tokens=settings.extraction_max_tokens), timeout=35)
            result = snapshot(raw, coerce(MODULE_SPECS['module_1'], raw),
                              turns, 'synthetic-release-no-database')['m1_contract']
            actual = not result['missing_fields']
            print(json.dumps({'live_case': name, 'seconds': round(perf_counter()-started, 3),
                'ready': actual, 'expected_ready': ready, 'missing_fields': result['missing_fields'],
                'validation_issues': result['validation_issues'], 'finish_reason': completion.finish_reason,
                'consent_turn': result['evidence'].get('consent', {}).get('turn'),
                'database_operations': 0}, ensure_ascii=False), flush=True)
            assert completion.finish_reason == 'stop', 'Extraction did not finish normally'
            assert actual == ready, 'M1 release live probe failed: ' + name
        finally:
            await provider._client.close()


async def main():
    await synthetic()
    if '--live' in sys.argv:
        await live()


if __name__ == '__main__':
    asyncio.run(main())
