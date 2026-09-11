"""Paired offline gate evaluation, or aggregate redacted retrieval_gate server logs.

No DB, generation model, import, migration, or production connection. Existing
evaluation labels are reused unchanged. Timing includes query construction and
actual retrieval, excludes loading/warmup, logging, network and generation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.nodes import MODULE_CONFIGS, _knowledge_query
from app.retrieval import RetrievalPolicy, rank_candidates, select_candidates
from app.retrieval_intent import GATE_VERSION, decide_retrieval
from app.schemas import Message
from scripts.evaluate_retrieval import load_corpus, measure, relevant_ids, summarize

BACKEND = Path(__file__).resolve().parents[1]


def latency(values):
    values = sorted(values)
    if not values:
        return {"mean_ms": None, "p50_ms": None, "p95_ms": None, "samples": 0}
    return {"mean_ms": round(sum(values) / len(values), 4),
            "p50_ms": round(values[math.ceil(len(values) * .5) - 1], 4),
            "p95_ms": round(values[math.ceil(len(values) * .95) - 1], 4), "samples": len(values)}


def make_state(case):
    history = [Message(**m) if isinstance(m, dict) else Message(role="user", content=m)
               for m in case.get("history", [])]
    return {"user_input": case["query"], "chat_history": history,
            "memory": {**case.get("memory", {}), **({"pa_card": case["pa_card"]} if case.get("pa_card") else {})},
            "clinical_context": case.get("clinical_context", [])}


def gate_contract(cases):
    rows = []
    for case in cases:
        decision = decide_retrieval(make_state(case))
        rows.append({"id": case["id"], "expected_retrieve": case["retrieve"], **decision.as_dict()})
    return {"cases": len(rows), "correct": sum(r["retrieve"] == r["expected_retrieve"] for r in rows),
            "false_skips": sum(r["expected_retrieve"] and not r["retrieve"] for r in rows),
            "missed_skips": sum(not r["expected_retrieve"] and r["retrieve"] for r in rows), "rows": rows}


def evaluate_group(cases, corpus, search, modes, repeats):
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Empty dataset or duplicate IDs")
    labels = {c["id"]: relevant_ids(c, corpus) for c in cases}
    rows, times = [], {mode: {"before": [], "after": []} for mode in modes}
    gate_times = []
    for index, case in enumerate(cases):
        state = make_state(case)
        decision = decide_retrieval(state)
        row = {"id": case["id"], "kind": case["kind"], "module": case["module"],
               "query": case["query"], "expect_empty": case["expect_empty"], "gate": decision.as_dict()}
        k = MODULE_CONFIGS[case["module"]].top_k
        for mode in modes:
            results, samples = {}, {"before": [], "after": []}
            for repeat in range(repeats):
                # Paired independent calls with alternating order reduce warm-cache bias.
                order = ("before", "after") if (index + repeat) % 2 == 0 else ("after", "before")
                for arm in order:
                    started = perf_counter()
                    if arm == "after":
                        gate_started = perf_counter()
                        current = decide_retrieval(state)
                        gate_times.append((perf_counter() - gate_started) * 1000)
                        should_search = current.retrieve
                    else:
                        should_search = True
                    hits = search(mode, case["module"], _knowledge_query(state), k) if should_search else []
                    samples[arm].append((perf_counter() - started) * 1000)
                    identity = [(h.id, h.score) for h in hits]
                    if arm in results and results[arm][0] != identity:
                        raise ValueError("Non-deterministic retrieval across repetitions")
                    results[arm] = (identity, hits)
            row[mode] = {}
            for arm in ("before", "after"):
                hits = results[arm][1]
                row[mode][arm] = {
                    **measure(hits, labels[case["id"]], expect_empty=case["expect_empty"], k=k),
                    "elapsed_ms": round(sum(samples[arm]) / repeats, 4),
                    "context_chars": sum(len(h.text) for h in hits),
                    "hits": [{"id": h.id, "score": h.score, "source": h.source} for h in hits]}
                times[mode][arm].extend(samples[arm])
        rows.append(row)
    summary = {}
    for mode in modes:
        summary[mode] = {}
        for arm in ("before", "after"):
            summary[mode][arm] = {
                **summarize([r[mode][arm] for r in rows]),
                "positive_precision_returned": summarize([r[mode][arm] for r in rows if not r["expect_empty"]])["precision_returned"],
                "latency": latency(times[mode][arm]),
                "mean_context_chars": round(sum(r[mode][arm]["context_chars"] for r in rows) / len(rows), 2)}
    positive = sum(not c["expect_empty"] for c in cases)
    false_skips = sum(not r["expect_empty"] and not r["gate"]["retrieve"] for r in rows)
    return {"summary": summary,
            "checks": {"no_positive_skips": false_skips == 0,
                       "pass_through_results_unchanged": all(r[m]["before"]["hits"] == r[m]["after"]["hits"]
                           for r in rows if r["gate"]["retrieve"] for m in modes)},
            "gate": {"version": GATE_VERSION, "evaluated": len(rows),
            "skipped": sum(not r["gate"]["retrieve"] for r in rows),
            "positive_cases": positive, "false_skips": false_skips,
            "false_skip_rate": false_skips / positive if positive else None,
            "reasons": dict(Counter(r["gate"]["reason"] for r in rows)), "latency": latency(gate_times)},
            "cases": rows}


def aggregate_logs(lines):
    rows, invalid = [], 0
    for line in lines:
        if "retrieval_gate " not in line:
            continue
        try:
            row = json.loads(line.split("retrieval_gate ", 1)[1])
            if (row["outcome"] not in {"skipped", "empty", "returned", "error"}
                    or not isinstance(row["gate"]["retrieve"], bool)
                    or any(not isinstance(row[key], (int, float)) or not math.isfinite(row[key]) or row[key] < 0
                           for key in ("gate_duration_ms", "search_duration_ms", "total_duration_ms", "returned", "context_chars"))):
                raise ValueError("Invalid metric")
            for key in ("module", "retriever"):
                if not isinstance(row[key], str):
                    raise ValueError("Invalid label")
            if not all(isinstance(row["gate"][key], str) for key in ("version", "reason")):
                raise ValueError("Invalid gate label")
            rows.append(row)
        except (ValueError, KeyError, TypeError):
            invalid += 1
    groups = {}
    for row in rows:
        key = "/".join((row["gate"]["version"], row["retriever"], row["module"],
                        "disabled" if row["gate"]["reason"] == "disabled" else "enabled"))
        groups.setdefault(key, []).append(row)
    result = {}
    for key, items in groups.items():
        count = len(items)
        outcomes = Counter(r["outcome"] for r in items)
        searches = sum(r["gate"]["retrieve"] for r in items)
        result[key] = {"turns": count, "search_calls": searches, "outcomes": dict(outcomes),
            "skip_rate": outcomes["skipped"] / count,
            "error_rate_per_search": outcomes["error"] / searches if searches else None,
            "search_empty_rate": outcomes["empty"] / searches if searches else None,
            "gate_latency": latency([r["gate_duration_ms"] for r in items]),
            "total_latency": latency([r["total_duration_ms"] for r in items]),
            "mean_returned": sum(r["returned"] for r in items) / count,
            "mean_context_chars": sum(r["context_chars"] for r in items) / count,
            "reasons": dict(Counter(r["gate"]["reason"] for r in items))}
    return {"events": len(rows), "invalid_events": invalid, "groups": result,
            "limitations": "Logs have no relevance labels: precision, correct_empty and false_skip_rate cannot be inferred. Not end-to-end reply latency. No raw queries retained."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--logs", type=Path, help="Aggregate a local journal/SSE-free server log file instead of evaluating")
    parser.add_argument("--with-vectors", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--vector-path", type=Path, default=BACKEND / ".rag-local/qdrant")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    if args.logs:
        with args.logs.open(encoding="utf-8-sig") as stream:
            report = aggregate_logs(stream)
    else:
        corpus, manifest = load_corpus()
        policy = RetrievalPolicy()
        kb = None
        def search(mode, module, query, k):
            if mode == "p0":
                return select_candidates(rank_candidates(corpus, module=module, query=query), top_k=k, policy=policy)
            return kb.search_sync(module=module, query=query, top_k=k, mode=mode)
        modes = ["p0"]
        try:
            if args.with_vectors:
                from app.vector_retrieval import LocalEmbedder, LocalVectorKnowledgeBase
                kb = LocalVectorKnowledgeBase(corpus, LocalEmbedder(BACKEND / ".rag-local/models"), args.vector_path)
                if not kb.ready():
                    raise RuntimeError("Index missing/incomplete; build explicitly with rag_local.py")
                modes += ["vector", "hybrid"]
            for mode in modes:
                search(mode, "module_2", "活动计划和散步", 4)
            report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                      "platform": platform.platform(), "python": platform.python_version(),
                      "gate_version": GATE_VERSION, "repeats": args.repeats, "chunks": len(corpus),
                      "manifest": manifest, "policy": asdict(policy), "datasets": {}, "groups": {},
                      "hashes": {path: hashlib.sha256((BACKEND / path).read_bytes()).hexdigest() for path in
                          ("app/retrieval_intent.py", "app/retrieval.py", "app/graph/nodes.py", "app/vector_retrieval.py",
                           "scripts/evaluate_intent_gate.py", "scripts/evaluate_retrieval.py")},
                      "limitations": "Existing dev/holdout were previously inspected: regression, not a new blind test. "
                          "Assistant-authored labels pending review. No answer-quality assessment. "
                          "Timing excludes model/index loading, DB, server logs, network and LLM generation; warm repeated local calls."}
            if kb:
                report["embedding"] = kb.embedder.identity
                report["collection"] = kb.collection
                report["hybrid_policy"] = asdict(kb.policy)
            for filename in ("retrieval_cases.json", "retrieval_vector_cases.json", "retrieval_intent_cases.json"):
                path = BACKEND / "evals" / filename
                data = json.loads(path.read_text(encoding="utf-8"))
                report["datasets"][filename] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "label_status": data["label_status"]}
                if filename == "retrieval_intent_cases.json":
                    report["gate_contract"] = gate_contract(data["cases"])
                    continue
                splits = ("dev", "holdout") if filename == "retrieval_cases.json" else ("all",)
                for split in splits:
                    cases = [c for c in data["cases"] if split == "all" or c["split"] == split]
                    key = f"{filename}/{split}"
                    print(f"Evaluating {key}: {len(cases)} cases", flush=True)
                    report["groups"][key] = evaluate_group(cases, corpus, search, modes, args.repeats)
        finally:
            if kb:
                kb.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report saved: {args.output}")


if __name__ == "__main__":
    main()
