"""1000 local SSE/custom-stream graph turns across M1-M4."""
from __future__ import annotations
import asyncio, json, runpy, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings
from app.graph import get_graph
from app.graph.state import GraphContext
from app.retrieval import StubKnowledgeBase
from app.session import InMemorySessionStore

stress = runpy.run_path(str(ROOT / 'infra/qa/m1-m4-stress-1000.py'))
StressProvider = stress['StressProvider']


async def main():
    total = 1000
    provider = StressProvider()
    settings = Settings(_env_file=None, knowledge_intent_gate_enabled=False,
                        knowledge_mediator_enabled=False, answer_validator_enabled=True)
    errors = []
    started = time.perf_counter()
    for i in range(total):
        store = InMemorySessionStore(ttl_seconds=3600, max_messages=40)
        context = GraphContext(provider=provider, router_provider=provider, store=store,
                               knowledge_base=StubKnowledgeBase(), settings=settings, stream=True)
        session = await store.get_or_create(None)
        module = f'module_{(i % 4) + 1}'
        events = [event async for event in get_graph().astream(
            {'session_id': session.session_id, 'user_input': f'本地流式验收第{i}轮',
             'forced_module': module, 'metadata': {}}, context=context, stream_mode='custom')]
        kinds = [event.get('type') for event in events]
        try:
            assert 'meta' in kinds and 'delta' in kinds and 'done' in kinds
            assert kinds.index('meta') < kinds.index('delta') < kinds.index('done')
            done = next(event for event in events if event.get('type') == 'done')
            assert done.get('reply_module') == module
            assert ''.join(event.get('text', '') for event in events if event.get('type') == 'delta')
        except Exception as exc:
            errors.append({'round': i, 'module': module, 'kinds': kinds, 'error': str(exc)})
    elapsed = time.perf_counter() - started
    report = {'scope': 'local synthetic only', 'streams': total, 'modules': 4,
              'provider_calls': {'stream': provider.stream_calls, 'detailed': provider.detailed_calls},
              'errors': errors, 'status': 'passed' if not errors else 'failed',
              'elapsed_seconds': round(elapsed, 3)}
    out = ROOT / '.test-tmp' / 'm1-m4-stream-stress-1000-report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'streams': total, 'errors': len(errors),
                      'elapsed_seconds': round(elapsed, 3), 'report': str(out)}))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
