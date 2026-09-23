"""Post-release synthetic streaming check. Two requests; no DB/login/user data."""
import asyncio
import json
import logging
from time import perf_counter
from app.config import get_settings
from app.generation_policy import main_thinking_options
from app.prompts import build_system_segments
from app.providers.deepseek import DeepSeekProvider
from app.providers.doubao import DoubaoProvider
from app.retrieval_intent import decide_retrieval
from app.schemas import Message


async def main():
    logging.disable(logging.CRITICAL)
    cfg = get_settings()
    assert cfg.chat_fast_ack_enabled and cfg.risk_gate_enabled and cfg.answer_validator_enabled
    assert cfg.module_router_reasoning_effort == 'provider_default'
    assert cfg.deepseek_reasoning_effort == 'provider_default'
    previous = '每周五天晚饭后在小区散步15分钟，下雨改室内走。你愿意按这个安排试试吗？'
    messages = [Message(role='user', content='我想每周五天晚饭后在小区散步15分钟，下雨室内走。'),
                Message(role='assistant', content=previous), Message(role='user', content='好的')]
    assert decide_retrieval({'user_input': '好的', 'chat_history': messages[:-1]}).reason == 'dialogue_confirmation'
    assert decide_retrieval({'user_input': '好的', 'chat_history': [Message(role='assistant',
        content='你愿意听我解释行为激活的原理吗？')]}).retrieve
    system = build_system_segments('module_2', clinical_context=[
        '合成检查，不是真实用户。plan_confirmed=false，不得声称保存成功或切换模块。'])

    async def one(cls):
        provider = cls(cfg)
        provider._client = provider._client.with_options(max_retries=0, timeout=25)
        assert main_thinking_options(cfg, provider.name, messages)['thinking']['type'] == 'disabled'
        started, first_content = perf_counter(), None
        text, reasoning, usage, finish = '', '', {}, None
        try:
            async def consume():
                nonlocal first_content, text, reasoning, usage, finish
                async for delta in provider.stream(system=system, messages=messages):
                    if delta.kind == 'content':
                        if delta.text and first_content is None:
                            first_content = perf_counter() - started
                        text += delta.text
                    elif delta.kind == 'reasoning':
                        reasoning += delta.text
                    elif delta.kind == 'usage':
                        usage, finish = delta.usage, delta.finish_reason
            await asyncio.wait_for(consume(), 30)
            passed = bool(text) and not reasoning and usage.get('reasoning_tokens', 0) == 0 and finish == 'stop'
            print(json.dumps({'provider': provider.name, 'model': provider.model, 'passed': passed,
                'first_content_seconds': round(first_content, 3) if first_content is not None else None,
                'total_seconds': round(perf_counter() - started, 3), 'usage': usage,
                'finish': finish, 'reply_chars': len(text)}, ensure_ascii=False), flush=True)
            return passed
        finally:
            await provider._client.close()

    results = await asyncio.gather(one(DeepSeekProvider), one(DoubaoProvider))
    if not all(results):
        raise RuntimeError('Live provider streaming verification failed')


if __name__ == '__main__':
    asyncio.run(main())
