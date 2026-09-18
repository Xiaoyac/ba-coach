"""Reproducible Dev-only grid; no Holdout/blind cases enter model selection."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.retrieval import KnowledgeChunk
from app.retrieval_enhanced import EnhancedKnowledgeRanker, EnhancedPolicy
from scripts.compare_retrieval_0917 import evaluate_retriever
from scripts.evaluate_retrieval import DEFAULT_CASES, load_corpus, measure, summarize

ROOT = Path(__file__).resolve().parents[1]


def lexical_grid(corpus, cases):
    # Candidate ordering is independent of these selection-only parameters.
    ranker = EnhancedKnowledgeRanker(corpus)
    cache = {}
    original = ranker.candidates

    def cached(**kwargs):
        key = tuple(sorted(kwargs.items()))
        if key not in cache:
            cache[key] = original(**kwargs)
        return cache[key]

    ranker.candidates = cached
    rows = []
    for coverage in (.12, .2, .25, .3):
        for relative in (.25, .4, .55, .7):
            ranker.policy = EnhancedPolicy(min_coverage=coverage, relative_score=relative)
            result = evaluate_retriever(cases, corpus, lambda module, query, top_k:
                ranker.search(module=module, query=query, top_k=top_k))
            rows.append({"policy": asdict(ranker.policy), "summary": result["summary"]})
    return rows


def rerank_grid(data):
    if len(data["rows"]) != 44 or any(not row["id"].startswith("dev") for row in data["rows"]):
        raise ValueError("Only the complete frozen Dev score file is accepted")
    results = []
    for floor in (-6, -4, -3, -2, -1, 0, 1, 2):
        for gap in (.5, 1, 2, 3, 5, 10):
            metrics = []
            for row in data["rows"]:
                hits, counts = [], {}
                cutoff = max(floor, row["ranked"][0]["score"] - gap) if row["ranked"] else floor
                for item in row["ranked"]:
                    source = item["source_id"]
                    if item["score"] < cutoff or counts.get(source, 0) >= 2:
                        continue
                    hits.append(KnowledgeChunk(item["id"], "", "", item["score"]))
                    counts[source] = counts.get(source, 0) + 1
                    if len(hits) == row["top_k"]:
                        break
                metrics.append(measure(hits, set(row["relevant"]), expect_empty=row["expect_empty"], k=row["top_k"]))
            results.append({"policy": {"min_logit": floor, "max_logit_gap": gap}, "summary": summarize(metrics)})
    return results


def main():
    corpus, manifest = load_corpus()
    cases = [c for c in json.loads(DEFAULT_CASES.read_text(encoding="utf-8"))["cases"] if c["split"] == "dev"]
    report = {"dataset": DEFAULT_CASES.name, "split": "dev", "case_count": len(cases),
        "dataset_sha256": hashlib.sha256(DEFAULT_CASES.read_bytes()).hexdigest(), "manifest": manifest,
        "lexical": lexical_grid(corpus, cases)}
    score_path = ROOT / ".rag-local/experiments0917/dev_scores.json"
    if score_path.exists():
        data = json.loads(score_path.read_text(encoding="utf-8"))
        if len(data["rows"]) == 44:
            report["semantic"] = rerank_grid(data)
            report["semantic_score_sha256"] = hashlib.sha256(score_path.read_bytes()).hexdigest()
            traces = [r["trace"] for r in data["rows"] if r["trace"]]
            times = sorted(t["inference_ms_original"] for t in traces)
            report["semantic_inference"] = {"model": traces[0]["model"] if traces else None,
                "scored_queries": len(traces), "cache_hits": sum(t["cache_hit"] for t in traces),
                "original_inference_mean_ms": sum(times) / len(times) if times else None,
                "original_inference_p95_ms": times[int((len(times)-1)*.95)] if times else None,
                "average_candidate_count": sum(t["candidate_count"] for t in traces)/len(traces) if traces else None,
                "latency_note": "Original measured inference durations, NOT the wall time of a cache hit. Excludes model loading."}
    path = ROOT / "evals/reports/rag0917/dev_tuning.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(path), "lexical_trials": len(report["lexical"]),
        "semantic_trials": len(report.get("semantic", []))}))


if __name__ == "__main__":
    main()
