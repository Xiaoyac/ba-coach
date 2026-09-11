"""Build, query and compare local P0/vector/hybrid retrieval; no business DB writes."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.nodes import MODULE_CONFIGS, _knowledge_query
from app.schemas import Message
from app.vector_retrieval import DEFAULT_MODEL, HybridPolicy, LocalEmbedder, LocalVectorKnowledgeBase
from scripts.evaluate_retrieval import DEFAULT_CASES, load_corpus, measure, relevant_ids, summarize

ROOT = Path(__file__).resolve().parents[1] / ".rag-local"


def evaluate(kb, cases):
    rows = []
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate case IDs")
    # Resolve ALL labels before embedding/search, so corpus drift fails early.
    labels = {case["id"]: relevant_ids(case, kb.corpus) for case in cases}
    for case in cases:
        query = _knowledge_query({"user_input": case["query"],
            "chat_history": [Message(role="user", content=q) for q in case.get("history", [])],
            "memory": {"pa_card": case["pa_card"]} if case.get("pa_card") else {}})
        k = MODULE_CONFIGS[case["module"]].top_k
        row = {"id": case["id"], "kind": case["kind"], "module": case["module"], "query": query}
        for mode in ("p0", "vector", "hybrid"):
            start = perf_counter()
            hits = kb.search_sync(module=case["module"], query=query, top_k=k, mode=mode)
            row[mode] = {**measure(hits, labels[case["id"]], expect_empty=case["expect_empty"], k=k),
                         "elapsed_ms": round((perf_counter() - start) * 1000, 3),
                         "hits": [{"id": hit.id, "source": hit.source, "score": hit.score} for hit in hits]}
        rows.append(row)
    return {"summary": {mode: summarize([row[mode] for row in rows])
                        for mode in ("p0", "vector", "hybrid")},
            "by_kind": {kind: {mode: summarize([r[mode] for r in rows if r["kind"] == kind])
                               for mode in ("p0", "vector", "hybrid")}
                        for kind in sorted({row["kind"] for row in rows})}, "cases": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "query", "evaluate"))
    parser.add_argument("--path", type=Path, default=ROOT / "qdrant")
    parser.add_argument("--cache", type=Path, default=ROOT / "models")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--mode", choices=("p0", "vector", "hybrid"), default="hybrid")
    parser.add_argument("--query")
    parser.add_argument("--module", choices=tuple(MODULE_CONFIGS), default="module_2")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--vector-min-score", type=float, default=0.5)
    parser.add_argument("--candidates", type=int, default=20)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "query" and not args.query:
        parser.error("query requires --query")
    corpus, manifest = load_corpus()
    print(f"Corpus: {len(corpus)} chunks. Loading local embedding model...", flush=True)
    embedder = LocalEmbedder(args.cache, args.model)
    kb = LocalVectorKnowledgeBase(corpus, embedder, args.path, mode=args.mode,
        policy=HybridPolicy(candidates=args.candidates, vector_min_score=args.vector_min_score))
    try:
        if args.command == "build":
            start = perf_counter()
            changed = kb.build()
            result = {"built": changed, "chunks": len(corpus), "seconds": round(perf_counter() - start, 2),
                      "collection": kb.collection, "embedding": embedder.identity}
        elif args.command == "query":
            result = {"mode": args.mode, "hits": [asdict(hit) for hit in kb.search_sync(
                module=args.module, query=args.query, top_k=args.top_k)]}
        else:
            data = json.loads(args.cases.read_text(encoding="utf-8"))
            cases = [c for c in data["cases"] if args.split == "all" or c["split"] == args.split]
            if not cases:
                parser.error("Selected split is empty")
            result = evaluate(kb, cases)
            result.update({"schema_version": 1, "split": args.split, "manifest": manifest,
                "chunks": len(corpus), "collection": kb.collection, "embedding": embedder.identity,
                "policy": asdict(kb.policy), "label_status": data["label_status"],
                "dataset_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
                "limitations": "Assistant-authored labels; not clinical/answer-quality validation. "
                    "Cosine threshold is experimental. Latency excludes model initialization; "
                    "vector and hybrid each include their own query embedding. No reranker.",
                "code_sha256": hashlib.sha256(
                    (Path(__file__).resolve().parents[1] / "app/vector_retrieval.py").read_bytes()).hexdigest()})
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result.get("summary", result), ensure_ascii=False, indent=2))
    finally:
        kb.close()


if __name__ == "__main__":
    main()
