"""Check graph delta delivery using isolated memory; no database writes.

Default: deterministic provider blocks until the consumer receives chunk one.
--real-provider: call the configured main model with a synthetic input instead.
Neither mode creates a production conversation or calls the business Router.
"""
import asyncio
import json
import logging
import sys
from time import perf_counter

from app.config import get_settings
from app.graph import get_graph
from app.generation_control import GenerationControl
from app.graph.state import GraphContext
from app.providers.base import Completion, LLMProvider, StreamDelta
from app.retrieval import StubKnowledgeBase
from app.session import InMemorySessionStore


class ProbeProvider(LLMProvider):
    name = 'streaming-probe'
    model = 'synthetic'

    def __init__(self):
        self.received = asyncio.Event()
        self.finished = False
        self.finished_at = None

    async def complete(self, **kwargs):
        return Completion(text='第一段。第二段。', model=self.model)

    async def stream(self, **kwargs):
        yield StreamDelta(kind='content', text='第一段。')
        await asyncio.wait_for(self.received.wait(), timeout=3)
        yield StreamDelta(kind='content', text='第二段。')
        self.finished = True
        self.finished_at = perf_counter()

    async def classify(self, *, default, **kwargs):
        return default

    async def route(self, **kwargs):
        return '{}'


async def main():
    logging.disable(logging.CRITICAL)
    settings = get_settings()
    probe = ProbeProvider()
    main_provider = probe
    real_provider = '--real-provider' in sys.argv
    if real_provider:
        from app.providers.deepseek import DeepSeekProvider
        main_provider = DeepSeekProvider(settings)
        original_stream = main_provider.stream

        async def measured_stream(**kwargs):
            async for delta in original_stream(**kwargs):
                yield delta
            probe.finished = True
            probe.finished_at = perf_counter()
        main_provider.stream = measured_stream
    context = GraphContext(provider=main_provider, router_provider=probe,
        store=InMemorySessionStore(ttl_seconds=60, max_messages=40),
        knowledge_base=StubKnowledgeBase(), settings=settings, stream=True)
    control = GenerationControl()
    context.generation = control
    started = perf_counter()
    first = None
    chunks = []
    final = None
    received_before_eof = False
    try:
        async for kind, value in control.iterate(get_graph().astream(
                {'user_input': '这是合成连通测试，请用四个完整句子说明散步如何开始，不涉及个人记录。',
                 'forced_module': 'module_1'}, context=context,
                stream_mode=['custom', 'values'])):
            if kind == 'values':
                final = value
            elif value.get('type') == 'delta' and value.get('text'):
                if first is None:
                    first = perf_counter()
                    received_before_eof = not probe.finished
                chunks.append(value['text'])
                probe.received.set()
        assert received_before_eof, 'No visible delta before provider EOF'
        assert final and not final.get('error'), 'Graph generation failed'
        assert ''.join(chunks) == final['final_response'], 'Stream/final text mismatch'
        stored = await context.store.get(final['session_id'])
        assert stored.messages[-1].content == final['final_response'], 'Stored text mismatch'
        print(json.dumps({'passed': True, 'real_provider': real_provider,
            'first_delta_ms': round((first-started)*1000),
            'generation_eof_ms': round((probe.finished_at-started)*1000),
            'visible_chunks': len(chunks), 'first_delta_before_eof': received_before_eof,
            'stream_matches_history': True, 'database_writes': 0}))
    finally:
        if real_provider:
            await main_provider._client.close()


if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(main(), timeout=90))
