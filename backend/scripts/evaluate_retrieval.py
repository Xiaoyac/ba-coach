"""Offline evaluation against the actual curated corpus; never opens a DB or LLM.

Run from backend: python scripts/evaluate_retrieval.py --split dev --output report.json
Judgments resolve against source/heading/text BEFORE retrieval, never from hits.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.nodes import MODULE_CONFIGS, _knowledge_query
from app.knowledge_store import chunk_markdown, KNOWLEDGE_CATEGORY_MODULES
from app.retrieval import RetrievalPolicy, index_chunk, rank_candidates, select_candidates
from app.schemas import Message
from scripts.import_project_knowledge import DEFAULT_KNOWLEDGE_DIR, _read_source, _source_paths

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "evals" / "retrieval_cases.json"


def load_corpus(directory=DEFAULT_KNOWLEDGE_DIR):
    documents, manifest = [], []
    for source_id, (path, category) in enumerate(_source_paths(directory), 1):
        content = _read_source(path)
        manifest.append({"source": path.name, "category": category,
                         "sha256": hashlib.sha256(content.encode()).hexdigest()})
        for chunk in chunk_markdown(content):
            documents.append(index_chunk(
                id=len(documents) + 1, source_id=source_id, source_name=path.name,
                category=category, heading=chunk.heading, content=chunk.content,
            ))
    return tuple(documents), manifest


def relevant_ids(case, corpus):
    """Validate every judgment; corpus drift is an error, not a silent zero."""
    if bool(case["relevant"]) == case["expect_empty"]:
        raise ValueError(f"{case['id']}: inconsistent empty/relevant labels")
    ids = set()
    for selector in case["relevant"]:
        matched = {
            f"kb:{d.id}" for d in corpus
            if d.source_name == selector["source"]
            and selector.get("heading", "").casefold() in d.heading.casefold()
            and selector.get("contains", "").casefold() in d.content.casefold()
            and case["module"] in KNOWLEDGE_CATEGORY_MODULES[d.category]
        }
        if not matched:
            raise ValueError(f"{case['id']}: judgment no longer matches corpus: {selector}")
        ids.update(matched)
    return ids


def legacy_select(ranked, top_k):
    """Pre-P0 category-first policy on the unchanged lexical scores."""
    selected, seen, categories = [], set(), set()
    for c in ranked:
        if c.category in categories:
            continue
        selected.append(c.chunk)
        seen.add(c.chunk.id)
        categories.add(c.category)
        if len(selected) == top_k:
            return selected
    for c in ranked:
        if c.chunk.id not in seen:
            selected.append(c.chunk)
            if len(selected) == top_k:
                break
    return selected


def measure(hits, relevant, *, expect_empty, k):
    binary = [int(c.id in relevant) for c in hits]
    hit_count = sum(binary)
    ranks = [i + 1 for i, b in enumerate(binary) if b]
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return {
        "returned": len(hits),
        "hit_at_k": int(bool(ranks)) if not expect_empty else None,
        "recall_at_k": hit_count / len(relevant) if relevant else None,
        "precision_returned": hit_count / len(hits) if hits else None,
        "mrr": 1 / ranks[0] if ranks else (None if expect_empty else 0),
        "ndcg_at_k": sum(b / math.log2(i + 2) for i, b in enumerate(binary)) / ideal if ideal else None,
        "correct_empty": int(not hits) if expect_empty else None,
    }


def summarize(rows):
    keys = ("returned", "hit_at_k", "recall_at_k", "precision_returned", "mrr",
            "ndcg_at_k", "correct_empty", "elapsed_ms")
    def mean(key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(values) / len(values), 4) if values else None
    return {"cases": len(rows), **{k: mean(k) for k in keys}}


def evaluate(cases, corpus, policy):
    rows = []
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs")
    for case in cases:
        relevant = relevant_ids(case, corpus)
        state = {"user_input": case["query"],
                 "chat_history": [Message(role="user", content=q) for q in case.get("history", [])],
                 "memory": {"pa_card": case["pa_card"]} if case.get("pa_card") else {}}
        query = _knowledge_query(state)
        k = MODULE_CONFIGS[case["module"]].top_k
        started = perf_counter()
        ranked = rank_candidates(corpus, module=case["module"], query=query)
        rank_ms = (perf_counter() - started) * 1000
        result = {"id": case["id"], "kind": case["kind"], "query": query,
                  "module": case["module"], "relevant_ids": sorted(relevant), "top_k": k}
        for name, select in (("baseline", lambda: legacy_select(ranked, k)),
                             ("p0", lambda: select_candidates(ranked, top_k=k, policy=policy))):
            started = perf_counter()
            hits = select()
            elapsed = rank_ms + (perf_counter() - started) * 1000
            result[name] = {**measure(hits, relevant, expect_empty=case["expect_empty"], k=k),
                            "elapsed_ms": round(elapsed, 3),
                            "hits": [{"id": c.id, "source": c.source, "score": c.score} for c in hits]}
        # Record candidate evidence to diagnose filtering without storing any user data.
        result["candidates"] = [{"id": c.chunk.id, "score": c.chunk.score,
                                 "coverage": round(c.coverage, 4)} for c in ranked[:10]]
        rows.append(result)
    return {
        "policy": asdict(policy),
        "summary": {name: summarize([r[name] for r in rows]) for name in ("baseline", "p0")},
        "by_kind": {kind: {name: summarize([r[name] for r in rows if r["kind"] == kind])
                           for name in ("baseline", "p0")} for kind in sorted({r["kind"] for r in rows})},
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--export-review", type=Path,
                        help="Export contexts and blank answer/faithfulness review fields; no LLM calls")
    parser.add_argument("--min-score", type=float, default=RetrievalPolicy().min_score)
    parser.add_argument("--min-coverage", type=float, default=RetrievalPolicy().min_coverage)
    parser.add_argument("--relative-score", type=float, default=RetrievalPolicy().relative_score)
    parser.add_argument("--max-per-source", type=int, default=RetrievalPolicy().max_per_source)
    args = parser.parse_args()
    data = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = [c for c in data["cases"] if args.split == "all" or c["split"] == args.split]
    if not cases:
        parser.error("selected split is empty")
    corpus, manifest = load_corpus()
    report = evaluate(cases, corpus, RetrievalPolicy(args.min_score, args.min_coverage,
                                                    args.relative_score, args.max_per_source))
    report.update({"schema_version": 1, "split": args.split, "chunks": len(corpus),
                   "manifest": manifest, "label_status": data["label_status"],
                   "dataset_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
                   "retrieval_sha256": hashlib.sha256(
                       (Path(__file__).resolve().parents[1] / "app" / "retrieval.py").read_bytes()).hexdigest(),
                   "baseline": "pre-P0 category-first selection, shared unchanged lexical score; timing includes shared ranking/support work"})
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.export_review:
        by_id = {f"kb:{d.id}": d for d in corpus}
        review = {
            "instructions": "人工填写实际回答并逐条核对事实是否有上下文证据；未填写值不得当作通过。禁止把源文献案例当作当前用户事实。",
            "dataset_sha256": report["dataset_sha256"],
            "retrieval_sha256": report["retrieval_sha256"],
            "cases": [{"case_id": r["id"], "query": r["query"], "module": r["module"],
                       "contexts": [{**hit, "text": by_id[hit["id"]].content} for hit in r["p0"]["hits"]],
                       "actual_answer": None, "reviewer": None, "reviewed_at": None,
                       "supported_claims": None, "unsupported_claims": None,
                       "citation_correct": None, "notes": None} for r in report["cases"]],
        }
        args.export_review.parent.mkdir(parents=True, exist_ok=True)
        args.export_review.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("split", "chunks", "policy", "summary")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
