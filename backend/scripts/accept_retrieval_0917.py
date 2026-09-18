"""Freeze the chosen configuration first, then run unchanged paired acceptance.

The previously inspected Holdout is a regression split, not an untouched test.
The separately authored 0917 set is first evaluated only after this manifest is
created. Reuse after changing ranking code requires a new evaluation version;
this script refuses to quietly overwrite the freeze.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.retrieval import RetrievalPolicy
from app.retrieval_enhanced import EnhancedPolicy
from scripts.compare_retrieval_0917 import compare, enhanced_retriever, p0_retriever
from scripts.evaluate_retrieval import load_corpus

OUT = ROOT / "evals/reports/rag0917"
FREEZE = OUT / "selection_freeze.json"
FILES = ("app/retrieval.py", "app/retrieval_enhanced.py", "app/retrieval_intent.py",
    "app/graph/nodes.py", "app/semantic_retrieval.py", "scripts/evaluate_retrieval.py",
    "scripts/compare_retrieval_0917.py", "scripts/accept_retrieval_0917.py",
    "evals/retrieval_cases.json", "evals/retrieval_vector_cases.json", "evals/retrieval_blind_0917.json")


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "evaluate"))
    args = parser.parse_args()
    corpus, manifest = load_corpus()
    if args.action == "freeze":
        OUT.mkdir(parents=True, exist_ok=True)
        payload = {"frozen_at": datetime.now(timezone.utc).isoformat(), "hashes": hashes(),
            "chosen_method": "enhanced_bm25f_with_existing_intent_gate", "policy": asdict(EnhancedPolicy()),
            "baseline_policy": asdict(RetrievalPolicy()), "manifest": manifest, "chunks": len(corpus),
            "selection_data": "retrieval_cases.json dev only", "semantic_status": "dev_experiment_not_selected",
            "scope": "Same query builder, module scoping, top_k, gate, corpus and unchanged labels.",
            "disclaimer": "Old Holdout was previously inspected. New 0917 cases are synthetic and not expert labels."}
        with FREEZE.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        print(f"Frozen: {FREEZE}")
        return
    frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
    if frozen["hashes"] != hashes() or frozen["manifest"] != manifest or frozen["policy"] != asdict(EnhancedPolicy()):
        raise ValueError("Frozen code/data/config changed. Do not report a retuned blind result as untouched.")
    plan = (("dev", "retrieval_cases.json", "dev"), ("holdout", "retrieval_cases.json", "holdout"),
            ("stress", "retrieval_vector_cases.json", None), ("blind", "retrieval_blind_0917.json", None))
    for name, dataset, split in plan:
        source = json.loads((ROOT / "evals" / dataset).read_text(encoding="utf-8"))
        cases = [c for c in source["cases"] if split is None or c.get("split") == split]
        result = compare(cases, corpus, p0_retriever(corpus, RetrievalPolicy()), enhanced_retriever(corpus),
            gate_enabled=True, bootstrap_repeats=2000)
        result.update({"dataset": dataset, "split": split, "cases_count": len(cases),
            "label_status": source.get("label_status"), "freeze_sha256": hashlib.sha256(FREEZE.read_bytes()).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat()})
        destination = OUT / (name + "_accepted.json")
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({"dataset": name, "baseline": result["baseline"]["summary"],
                          "enhanced": result["enhanced"]["summary"]}), flush=True)


if __name__ == "__main__":
    main()
