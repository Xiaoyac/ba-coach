"""Read-only live-corpus acceptance. Generic fixtures, no user transcript/DB writes."""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db import dispose_db
from app.hybrid_knowledge_base import HybridDatabaseKnowledgeBase
from app.catalog_knowledge_base import CatalogDatabaseKnowledgeBase
from app.knowledge_mediator import mediate_knowledge
from app.providers import get_provider
from app.retrieval_intent import decide_retrieval

CASES = [
    ("activation_zh", "module_1", "为什么不能等心情好了再开始行动？"),
    ("walking", "module_2", "散步十分钟算身体活动吗？"),
    ("motivation", "module_3", "我知道应该行动，可就是没动力，如何开始？"),
    ("study_cycle", "module_4", "刚开始学习，过了一会又去打游戏，怎么理解这种循环？"),
    ("rejected_activity", "module_2", "我不想跳绳，只想散步"),
    ("activation_en", "module_1", "What is behavioral activation?"),
    ("out_of_domain", "module_1", "告诉我量子计算机价格"),
    ("greeting", "module_1", "你好"),
]


async def main(args):
    settings = get_settings()
    hybrid = HybridDatabaseKnowledgeBase()
    started = perf_counter()
    count = await hybrid.warmup()
    assert count > 0
    provider = get_provider().with_thinking(False) if args.compare else None
    legacy = CatalogDatabaseKnowledgeBase(provider) if provider else None
    report = {"corpus_chunks": count, "build_seconds": round(perf_counter() - started, 3),
        "embedding_identity": hybrid._embedder.identity, "database_writes": 0,
        "reranker": settings.knowledge_rerank_model, "cases": []}
    for name, module, query in CASES:
        row = {"id": name, "module": module, "query": query}
        state = {"user_input": query, "knowledge_task": "general", "memory": {}}
        gate = decide_retrieval(state)
        row["gate"] = gate.as_dict()
        if not gate.retrieve:
            report["cases"].append(row)
            continue
        hits, metrics = await hybrid.search_with_diagnostics(module=module, query=query,
            top_k=settings.knowledge_hybrid_final_results)
        assert metrics["status"] == "hybrid_completed", metrics
        details = metrics["retriever_details"]
        assert details["catalog_calls"] == details["mediator_calls"] == 0
        if settings.knowledge_hybrid_require_rerank:
            assert details["rerank"] == "completed", details
        row["hybrid"] = {"metrics": metrics, "hits": [asdict(hit) for hit in hits]}
        if name == "out_of_domain":
            assert not hits, "Unrelated question must not pull incidental word matches"
        if legacy:
            start = perf_counter()
            old, old_metrics = await legacy.search_with_diagnostics(module=module, query=query, top_k=4)
            selected, guidance, mediation = await mediate_knowledge(state=state, module=module,
                knowledge=old, provider=provider, settings=settings)
            row["legacy"] = {"total_ms": round((perf_counter() - start) * 1000, 3),
                "metrics": old_metrics, "mediator": mediation, "guidance": guidance,
                "recalled": [asdict(hit) for hit in old], "provided": [asdict(hit) for hit in selected]}
        report["cases"].append(row)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"case": name, "hybrid_ms": metrics["duration_ms"], "hybrid_hits": len(hits),
            "legacy_ms": row.get("legacy", {}).get("total_ms")}, ensure_ascii=False), flush=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    await dispose_db()
    print("PASS: read-only corpus acceptance", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", action="store_true", help="Make bounded real legacy model calls")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
