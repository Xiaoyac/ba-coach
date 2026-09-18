"""Explicit opt-in paid synthetic experiment. No production graph or DB changes.

Only prompts, queries and public knowledge are sent through the SSH worker;
no relevance judgments, expected answers or label rationale enter model input.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.semantic_catalog import SemanticCatalog, PLAN_PROMPT, JUDGE_PROMPT, VERSION
from app.graph.nodes import MODULE_CONFIGS, _knowledge_query
from app.retrieval_intent import decide_retrieval
from scripts.compare_retrieval_0917 import state_for, evaluate_retriever, p0_retriever, bootstrap_deltas, summarize_rows
from scripts.evaluate_retrieval import load_corpus
from app.retrieval import RetrievalPolicy


def call_batch(system, jobs, *, key, host, cache):
    if not jobs:
        return {}
    payload = {"system":system,"jobs":jobs}
    fingerprint = hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    path = cache / (fingerprint + ".json")
    job_paths = {job["id"]:cache / ("job-" + hashlib.sha256(json.dumps(
        {"system":system,"payload":job["payload"],"model":"deepseek-v4.1-flash"},
        ensure_ascii=False,sort_keys=True).encode()).hexdigest() + ".json") for job in jobs}
    if path.exists():
        results = json.loads(path.read_text(encoding="utf-8"))["results"]
        for identifier,result in results.items():
            job_paths[identifier].write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8")
        return results
    results = {}
    for identifier, job_path in job_paths.items():
        if job_path.exists():
            results[identifier] = {**json.loads(job_path.read_text(encoding="utf-8")),"id":identifier}
    pending = [job for job in jobs if job["id"] not in results]
    if not pending:
        return results
    payload = {"system":system,"jobs":pending}
    command = ["ssh", "-i", str(key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host,
        "set -a; . /etc/bacoach/backend.env; . /etc/bacoach/workbench-safety.env; set +a; "
        "cd /opt/bacoach/current/backend && PYTHONPATH=/opt/bacoach/current/backend "
        ".venv/bin/python /tmp/bacoach-flash-rag-batch-0917.py"]
    process = subprocess.run(command, input=json.dumps(payload,ensure_ascii=False).encode(), capture_output=True, timeout=1800)
    if process.returncode:
        raise RuntimeError("SSH experiment worker failed; no silent baseline fallback")
    result = json.loads(process.stdout)
    results.update({r["id"]:r for r in result["results"]})
    if len(results) != len(jobs) or set(results) != {job["id"] for job in jobs}:
        raise ValueError("Incomplete model batch")
    path.write_text(json.dumps({"fingerprint":fingerprint,"results":results},ensure_ascii=False),encoding="utf-8")
    for identifier,result in results.items():
        job_paths[identifier].write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="retrieval_cases.json")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fast-path", action="store_true", help="Skip model calls for explicit current-turn chapter-title requests")
    args = parser.parse_args()
    corpus, manifest = load_corpus()
    dataset = ROOT / "evals" / args.dataset
    cases = [c for c in json.loads(dataset.read_text(encoding="utf-8"))["cases"]
             if args.split == "all" or c.get("split") == args.split]
    if not cases:
        raise ValueError("Empty dataset split")
    cache = ROOT / ".rag-local/catalog0917"
    cache.mkdir(parents=True,exist_ok=True)
    catalog = SemanticCatalog(corpus)
    queries = {c["id"]:_knowledge_query(state_for(c)) for c in cases}
    active = [c for c in cases if decide_retrieval(state_for(c)).retrieve]
    fast = {}
    if args.fast_path:
        for case in active:
            hits = catalog.fast_path(module=case["module"],query=queries[case["id"]],top_k=MODULE_CONFIGS[case["module"]].top_k)
            if hits is not None:
                fast[case["id"]] = hits
        active = [c for c in active if c["id"] not in fast]
    plans = call_batch(PLAN_PROMPT, [{"id":c["id"],"payload":catalog.plan_payload(module=c["module"],query=queries[c["id"]])}
                                   for c in active],key=args.key,host=args.host,cache=cache)
    print(f"Planned {len(plans)} queries", flush=True)
    docs, errors, jobs = {}, {}, []
    for case in active:
        identifier = case["id"]
        try:
            plan = plans[identifier]
            if plan.get("error") or plan.get("finish_reason") != "stop":
                raise ValueError("plan_provider_error")
            docs[identifier] = catalog.candidates(module=case["module"],query=queries[identifier],plan_raw=plan["text"])
            if docs[identifier]:
                jobs.append({"id":identifier,"payload":catalog.judge_payload(module=case["module"],query=queries[identifier],
                    documents=docs[identifier],top_k=MODULE_CONFIGS[case["module"]].top_k)})
        except (ValueError,TypeError,KeyError) as exc:
            docs[identifier] = []
            errors[identifier] = type(exc).__name__ + ":" + str(exc)
    judgments = call_batch(JUDGE_PROMPT,jobs,key=args.key,host=args.host,cache=cache)
    print(f"Judged {len(judgments)} candidate groups",flush=True)
    final, trace = {}, {}
    for case in cases:
        identifier = case["id"]
        hits = fast.get(identifier, [])
        if identifier in judgments:
            result = judgments[identifier]
            try:
                if result.get("error") or result.get("finish_reason") != "stop":
                    raise ValueError("judge_provider_error")
                hits = catalog.results(result["text"], docs[identifier], top_k=MODULE_CONFIGS[case["module"]].top_k)
            except (ValueError,TypeError,KeyError) as exc:
                errors[identifier] = type(exc).__name__ + ":" + str(exc)
        final[(case["module"],queries[identifier])] = hits
        trace[identifier] = {"plan":plans.get(identifier),"judgment":judgments.get(identifier),
            "candidate_ids":[f"kb:{d.id}" for d in docs.get(identifier,[])], "error":errors.get(identifier),
            "fast_path":identifier in fast}
    after = evaluate_retriever(cases,corpus,lambda module,query,top_k:final[(module,query)])
    for row in after["cases"]:
        row["cached_lookup_ms"] = row["elapsed_ms"]
        t = trace[row["id"]]
        row["elapsed_ms"] = sum((t.get(stage) or {}).get("duration_ms", 0) for stage in ("plan", "judgment"))
    after["summary"] = summarize_rows(after["cases"])
    after["by_kind"] = {kind:summarize_rows([r for r in after["cases"] if r["kind"] == kind])
                        for kind in sorted({r["kind"] for r in after["cases"]})}
    before = evaluate_retriever(cases,corpus,p0_retriever(corpus,RetrievalPolicy()))
    report = {"version":VERSION,"dataset":args.dataset,"split":args.split,"manifest":manifest,"fast_path":args.fast_path,
        "dataset_sha256":hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "source_sha256":hashlib.sha256((ROOT / "app/semantic_catalog.py").read_bytes()).hexdigest(),
        "baseline":before,"catalog":after,"delta":bootstrap_deltas(before["cases"],after["cases"],repeats=2000),
        "trace":trace,"errors":errors,
        "latency_note":"catalog elapsed_ms is original remote plan+judgment API duration, even when replaying cached responses; excludes model queue/SSH/DB/main generation. Not a live service SLA."}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"baseline":before["summary"],"catalog":after["summary"],"errors":errors},ensure_ascii=False))


if __name__ == "__main__":
    main()
