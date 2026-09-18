"""Probe real mediator/router contracts with invented messages, no DB writes."""
import asyncio
import json
import logging
from app.config import get_settings
from app.providers.deepseek import DeepSeekProvider
from app.knowledge_mediator import mediate_knowledge
from app.router_agent import decide_target_module_with_reasoning
from app.retrieval import KnowledgeChunk


async def main():
    logging.disable(logging.CRITICAL)
    settings = get_settings()
    provider = DeepSeekProvider(settings)
    try:
        selected, block, metrics = await mediate_knowledge(
            state={"user_input":"什么是活动监测？"}, module="module_1",
            knowledge=[KnowledgeChunk("synthetic-1", "活动监测是记录日常活动与当时情绪，帮助发现两者关系。", "synthetic-example")],
            provider=provider, settings=settings)
        router = await asyncio.wait_for(decide_target_module_with_reasoning(provider,
            current_module="module_2", user_input="我还没确定活动，先了解一下散步。",
            ai_output="可以先了解，不必急着决定。", has_pa_card=False), 45)
        print(json.dumps({"mediator": {k:metrics.get(k) for k in
            ("status", "reason", "model", "usage", "duration_ms", "approved_chunk_count", "withheld_on_error")},
            "selected_ids":[c.id for c in selected],
            "router": {k:getattr(router,k,None) for k in ("target_module","model","finish_reason","usage","error_code")},
            "router_result_fields": list(vars(router))}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error_type":type(exc).__name__}))
        raise SystemExit(1)
    finally:
        await provider._client.close()


if __name__ == "__main__":
    asyncio.run(main())
