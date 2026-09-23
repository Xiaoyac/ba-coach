"""Read-only production latency inspection. Never prints text, identities, or secrets."""
import asyncio
import json
import logging
import statistics
from collections import defaultdict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import get_settings


def distribution(values):
    values = sorted(v for v in values if v is not None)
    return {"n": len(values), "median_ms": round(statistics.median(values)),
            "p95_ms": values[min(len(values) - 1, int(len(values) * .95))], "max_ms": max(values)} if values else {"n": 0}


async def main():
    logging.disable(logging.CRITICAL)
    cfg = get_settings()
    # Allowlist only; do not dump Settings or URLs containing authentication.
    config = {k: getattr(cfg, k, None) for k in (
        'deepseek_model','deepseek_router_model','doubao_model','doubao_reasoning_effort',
        'knowledge_retrieval_mode','knowledge_mediator_timeout_seconds','knowledge_mediator_reasoning_effort',
        'risk_gate_enabled','answer_validator_enabled','database_schema_version')}
    engine = create_async_engine(cfg.database_url)
    try:
        async with engine.connect() as db:
            events = (await db.execute(text("""SELECT stage, model_name, duration_ms, input_tokens,
                output_tokens, reasoning_tokens, error_code FROM ai_execution_events
                WHERE created_at >= UTC_TIMESTAMP() - INTERVAL 3 DAY ORDER BY id DESC LIMIT 800"""))).mappings().all()
            turns = (await db.execute(text("""SELECT m.main_generation_duration_ms, m.risk_gate_duration_ms,
                m.router_duration_ms, m.time_to_first_content_token_ms, m.reasoning_tokens,
                m.model_name, p.content AS input, CHAR_LENGTH(m.content) AS reply_chars,
                TIMESTAMPDIFF(MICROSECOND,p.created_at,m.created_at)/1000 AS persisted_elapsed_ms
                FROM conversation_messages m JOIN conversation_messages p
                  ON m.conversation_id=p.conversation_id AND p.position=m.position-1 AND p.role='user'
                WHERE m.role='assistant' AND m.created_at >= UTC_TIMESTAMP()-INTERVAL 3 DAY
                ORDER BY m.id DESC LIMIT 150"""))).mappings().all()
        groups = defaultdict(list)
        for row in events:
            groups[(row['stage'], row['model_name'])].append(row)
        stages = [{"stage": stage, "model": model, **distribution([r['duration_ms'] for r in rows]),
                   "errors": sum(bool(r['error_code']) for r in rows),
                   "mean_input_tokens": round(statistics.mean([r['input_tokens'] or 0 for r in rows])),
                   "mean_output_tokens": round(statistics.mean([r['output_tokens'] or 0 for r in rows])),
                   "mean_reasoning_tokens": round(statistics.mean([r['reasoning_tokens'] or 0 for r in rows]))}
                  for (stage, model), rows in groups.items()]
        acks = []
        for row in turns:
            clean = row['input'].strip(' \n\t。！!，,')
            if clean in {'好','好的','嗯','嗯嗯','可以','我愿意','愿意','是的','对','明白了'}:
                acks.append({k: float(v) if k == 'persisted_elapsed_ms' and v is not None else v
                             for k, v in row.items() if k != 'input'})
        print(json.dumps({"config": config, "sample_events": len(events), "stages": stages,
                          "recent_ack_turns": acks[:20], "ack_count": len(acks),
                          "persisted_turn_elapsed": distribution([float(r['persisted_elapsed_ms']) for r in turns if r['persisted_elapsed_ms'] is not None])},ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
