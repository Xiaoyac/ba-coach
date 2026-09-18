"""Read-only DB and synthetic RAG checks; never creates production test users."""
import asyncio
import hashlib
import json
import logging
from time import perf_counter
from sqlalchemy import select
from app.config import get_settings
from app.db import get_sessionmaker, dispose_db
from app.database_v2_schema import metadata
from app.prompt_store import effective_prompt_pair, PROMPT_DEFINITION_BY_KEY
from app.prompts import MODULE_PROMPTS, build_system_prompt
from app.retrieval import get_knowledge_base
from app.knowledge_mediator import mediate_knowledge


async def main():
    logging.disable(logging.CRITICAL)
    settings = get_settings()
    knowledge = get_knowledge_base()
    assert type(knowledge).__name__ == "CatalogDatabaseKnowledgeBase"
    try:
        async with get_sessionmaker()() as db:
            for table in metadata.tables.values():
                await db.execute(select(table).limit(0))
            _, prompt = await effective_prompt_pair(db, "module_4")
            assert prompt == MODULE_PROMPTS["module_4"]
            assert PROMPT_DEFINITION_BY_KEY["module_3"].description == "计划执行前记录提醒"
            system = build_system_prompt("module_4", module_prompt=prompt)
            assert "m4-20260917-v1" in system and "独立 secondary" in system
            print(json.dumps({"schema": "all_v2_columns_readable", "effective_m4_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "module3_copy": "计划执行前记录提醒", "mode": settings.knowledge_retrieval_mode,
                "provider": settings.default_provider, "model": settings.deepseek_model,
                "router_model": settings.deepseek_router_model, "mediator": settings.knowledge_mediator_enabled}, ensure_ascii=False), flush=True)
        count = await knowledge.warmup()
        assert count > 0
        print(json.dumps({"knowledge_chunks": count}), flush=True)
        for query in ("什么是ABC功能分析？", "总等心情变好了再开始行动，这样合适吗？", "帮我查一下今天比特币价格"):
            start = perf_counter()
            hits = await knowledge.search(module="module_4", query=query, top_k=2)
            selected, _, metrics = await mediate_knowledge(state={"user_input": query}, module="module_4",
                knowledge=hits, provider=knowledge.provider, settings=settings)
            if "比特币" in query:
                assert not hits
            else:
                assert hits, "Synthetic positive retrieval unexpectedly empty"
            assert set(x.id for x in selected).issubset(x.id for x in hits)
            print(json.dumps({"synthetic_query": query, "hits": len(hits), "approved": len(selected),
                "mediator_status": metrics.get("status"), "elapsed_ms": round((perf_counter()-start)*1000)}, ensure_ascii=False), flush=True)
    finally:
        await dispose_db()
        if hasattr(knowledge.provider, "_client"):
            await knowledge.provider._client.close()


asyncio.run(main())
