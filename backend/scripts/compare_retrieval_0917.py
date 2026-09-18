"""Frozen, paired offline retrieval comparison for the 2026-09-17 experiment.

This utility deliberately evaluates a supplied retriever rather than importing an
experiment as a silent fallback.  It uses the production query builder and intent
gate, but does not open a database, call a generation model, or contact a service.

Example (from backend):
  python scripts/compare_retrieval_0917.py --dataset evals/retrieval_cases.json --split dev --output evals/baselines/p0_dev_0917.json
  python scripts/compare_retrieval_0917.py --mode both --output evals/reports/retrieval_comparison_0917.json
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from random import Random
import sys
from time import perf_counter
from typing import Callable, Iterable

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.graph.nodes import MODULE_CONFIGS, _knowledge_query
from app.retrieval import KnowledgeChunk, RetrievalPolicy, rank_candidates, select_candidates
from app.retrieval_intent import GATE_VERSION, decide_retrieval
from app.schemas import Message
from scripts.evaluate_retrieval import load_corpus, measure, relevant_ids, summarize

DEFAULT_DATASET = BACKEND / "evals" / "retrieval_blind_0917.json"
Retriever = Callable[[str, str, int], list[KnowledgeChunk]]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state_for(case: dict) -> dict:
    """Use the real query context contract; support legacy string histories."""
    history = [Message(**item) if isinstance(item, dict) else Message(role="user", content=item)
               for item in case.get("history", [])]
    memory = dict(case.get("memory", {}))
    if case.get("pa_card"):
        memory["pa_card"] = case["pa_card"]
    return {"user_input": case["query"], "chat_history": history, "memory": memory,
            "clinical_context": case.get("clinical_context", [])}


def p0_retriever(corpus, policy: RetrievalPolicy) -> Retriever:
    def search(module: str, query: str, top_k: int) -> list[KnowledgeChunk]:
        ranked = rank_candidates(corpus, module=module, query=query)
        return select_candidates(ranked, top_k=top_k, policy=policy)
    return search


def enhanced_retriever(corpus) -> Retriever:
    """Load the experiment explicitly.  Never mask a missing/broken ranker with P0."""
    from app.retrieval_enhanced import EnhancedKnowledgeRanker
    ranker = EnhancedKnowledgeRanker(corpus)

    def search(module: str, query: str, top_k: int) -> list[KnowledgeChunk]:
        hits = ranker.search(module=module, query=query, top_k=top_k)
        if not isinstance(hits, list) or any(not isinstance(hit, KnowledgeChunk) for hit in hits):
            raise TypeError("EnhancedKnowledgeRanker.search must return list[KnowledgeChunk]")
        return hits
    return search


def _mean(values: Iterable[float | int | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return round(sum(usable) / len(usable), 4) if usable else None


def summarize_rows(rows: list[dict]) -> dict:
    metrics = summarize([row["metrics"] for row in rows])
    positives = [row for row in rows if not row["expect_empty"]]
    negatives = [row for row in rows if row["expect_empty"]]
    metrics.update({
        "positive_precision_returned": _mean(row["metrics"]["precision_returned"] for row in positives),
        "false_empty": sum(not row["hits"] for row in positives),
        "false_empty_rate": round(sum(not row["hits"] for row in positives) / len(positives), 4) if positives else None,
        "positive_cases": len(positives),
        "empty_control_cases": len(negatives),
        "gate_skips": sum(not row["gate"]["retrieve"] for row in rows),
        "false_skips": sum(not row["expect_empty"] and not row["gate"]["retrieve"] for row in rows),
        "latency_mean_ms": _mean(row["elapsed_ms"] for row in rows),
        "latency_p50_ms": percentile([row["elapsed_ms"] for row in rows], .5),
        "latency_p95_ms": percentile([row["elapsed_ms"] for row in rows], .95),
    })
    return metrics


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))], 4)


def evaluate_retriever(cases: list[dict], corpus, search: Retriever, *, gate_enabled: bool = True) -> dict:
    """Evaluate exactly one retriever; errors intentionally propagate to the caller."""
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Dataset must be non-empty and have unique case IDs")
    rows = []
    for case in cases:
        relevant = relevant_ids(case, corpus)
        state = state_for(case)
        gate = decide_retrieval(state).as_dict() if gate_enabled else {
            "retrieve": True, "reason": "disabled", "version": GATE_VERSION}
        query = _knowledge_query(state)
        top_k = MODULE_CONFIGS[case["module"]].top_k
        started = perf_counter()
        hits = search(case["module"], query, top_k) if gate["retrieve"] else []
        elapsed_ms = round((perf_counter() - started) * 1000, 4)
        rows.append({"id": case["id"], "kind": case["kind"], "module": case["module"],
                     "expect_empty": case["expect_empty"], "query": query, "top_k": top_k,
                     "relevant_ids": sorted(relevant), "gate": gate,
                     "metrics": measure(hits, relevant, expect_empty=case["expect_empty"], k=top_k),
                     "elapsed_ms": elapsed_ms,
                     "hits": [{"id": hit.id, "source": hit.source, "score": hit.score} for hit in hits]})
    return {"summary": summarize_rows(rows),
            "by_kind": {kind: summarize_rows([row for row in rows if row["kind"] == kind])
                        for kind in sorted({row["kind"] for row in rows})},
            "gate_reasons": dict(Counter(row["gate"]["reason"] for row in rows)), "cases": rows}


DELTA_METRICS = ("hit_at_k", "recall_at_k", "precision_returned", "positive_precision_returned",
                 "correct_empty", "false_empty_rate", "mrr", "ndcg_at_k")


def _metric_from_sample(rows: list[dict], key: str) -> float | None:
    return summarize_rows(rows).get(key)


def bootstrap_deltas(before_rows: list[dict], after_rows: list[dict], *, repeats: int, seed: int = 917) -> dict:
    if len(before_rows) != len(after_rows) or [row["id"] for row in before_rows] != [row["id"] for row in after_rows]:
        raise ValueError("Paired bootstrap requires the same cases in the same order")
    random = Random(seed)
    result = {}
    for key in DELTA_METRICS:
        draws = []
        for _ in range(repeats):
            indices = [random.randrange(len(before_rows)) for _ in before_rows]
            before = _metric_from_sample([before_rows[i] for i in indices], key)
            after = _metric_from_sample([after_rows[i] for i in indices], key)
            if before is not None and after is not None:
                draws.append(after - before)
        result[key] = {"point_delta": rounded_delta(_metric_from_sample(before_rows, key), _metric_from_sample(after_rows, key)),
                       "bootstrap_95_ci": [percentile(draws, .025), percentile(draws, .975)],
                       "bootstrap_samples": len(draws)}
    return result


def rounded_delta(before: float | int | None, after: float | int | None) -> float | None:
    return round(after - before, 4) if before is not None and after is not None else None


def compare(cases: list[dict], corpus, baseline: Retriever, enhanced: Retriever | None, *,
            gate_enabled: bool, bootstrap_repeats: int) -> dict:
    baseline_result = evaluate_retriever(cases, corpus, baseline, gate_enabled=gate_enabled)
    result = {"baseline": baseline_result}
    if enhanced is not None:
        enhanced_result = evaluate_retriever(cases, corpus, enhanced, gate_enabled=gate_enabled)
        result["enhanced"] = enhanced_result
        result["delta"] = bootstrap_deltas(baseline_result["cases"], enhanced_result["cases"],
                                             repeats=bootstrap_repeats)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--split", default="all", help="Dataset split; default evaluates every frozen case")
    parser.add_argument("--mode", choices=("baseline", "enhanced", "both"), default="both")
    parser.add_argument("--disable-gate", action="store_true", help="Diagnostic only; baseline is gate-enabled")
    parser.add_argument("--bootstrap-repeats", type=int, default=2000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.bootstrap_repeats < 1:
        parser.error("--bootstrap-repeats must be positive")
    data = json.loads(args.dataset.read_text(encoding="utf-8"))
    cases = [case for case in data["cases"] if args.split == "all" or case.get("split") == args.split]
    if not cases:
        parser.error("selected split is empty")
    corpus, manifest = load_corpus()
    policy = RetrievalPolicy()
    baseline = p0_retriever(corpus, policy)
    enhanced = enhanced_retriever(corpus) if args.mode in ("enhanced", "both") else None
    if args.mode == "enhanced":
        report = {"enhanced": evaluate_retriever(cases, corpus, enhanced, gate_enabled=not args.disable_gate)}
    else:
        report = compare(cases, corpus, baseline, enhanced, gate_enabled=not args.disable_gate,
                         bootstrap_repeats=args.bootstrap_repeats)
    candidate = {"status": "not_evaluated"}
    if args.mode in ("enhanced", "both"):
        from app.retrieval_enhanced import EnhancedPolicy, VERSION
        candidate = {"status": "evaluated", "name": "EnhancedKnowledgeRanker", "version": VERSION,
                     "policy": asdict(EnhancedPolicy()),
                     "source_hashes": {"app/retrieval_enhanced.py": _sha256(BACKEND / "app/retrieval_enhanced.py")}}
    report.update({"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                   "dataset": args.dataset.name, "dataset_sha256": _sha256(args.dataset),
                   "label_status": data.get("label_status"), "cases": len(cases), "chunks": len(corpus),
                   "manifest": manifest, "gate_enabled": not args.disable_gate, "gate_version": GATE_VERSION,
                   "policy": asdict(policy), "hashes": {path: _sha256(BACKEND / path) for path in
                       ("app/retrieval.py", "app/graph/nodes.py", "app/retrieval_intent.py",
                        "scripts/evaluate_retrieval.py", "scripts/compare_retrieval_0917.py")},
                   "candidate": candidate,
                   "limitations": "Assistant-authored frozen labels require domain review; not a blinded expert or answer-quality evaluation. "
                       "Offline latency excludes database, model loading, network and generation. Enhanced errors are not hidden by P0 fallback."})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "cases": len(cases), "dataset_sha256": report["dataset_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
