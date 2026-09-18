"""Dev-only parameter exploration; never read holdout or fresh blind judgments."""
import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys
from time import perf_counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.graph.nodes import MODULE_CONFIGS, _knowledge_query
from app.schemas import Message
from app.retrieval_intent import decide_retrieval
from app.semantic_retrieval import HybridKnowledgeRanker, SemanticPolicy, ROOT
from scripts.evaluate_retrieval import load_corpus, DEFAULT_CASES, relevant_ids, measure, summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments0917" / "dev_scores.json")
    args = parser.parse_args()
    corpus, manifest = load_corpus()
    ranker = HybridKnowledgeRanker(corpus, score_cache=ROOT / "experiments0917" / "scores")
    cases = [x for x in json.loads(DEFAULT_CASES.read_text(encoding="utf-8"))["cases"] if x["split"] == "dev"]
    rows = []
    try:
        for index, case in enumerate(cases):
            state = {"user_input": case["query"], "chat_history": [Message(role="user",content=q) for q in case.get("history", [])],
                "memory": {"pa_card":case["pa_card"]} if case.get("pa_card") else {}}
            started = perf_counter()
            ranked = ranker.scored_candidates(module=case["module"], query=_knowledge_query(state)) if decide_retrieval(state).retrieve else []
            rows.append({"id":case["id"], "expect_empty":case["expect_empty"], "relevant":sorted(relevant_ids(case,corpus)),
                "top_k":MODULE_CONFIGS[case["module"]].top_k, "trace":dict(ranker.last_trace) if ranked else {},
                "elapsed_ms":(perf_counter()-started)*1000,
                "ranked":[{"id":f"kb:{d.id}","source_id":d.source_id,"score":score} for d,score in ranked]})
            print(f"dev {index+1}/{len(cases)} {case['id']}", flush=True)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps({"manifest":manifest,"rows":rows},ensure_ascii=False,indent=2),encoding="utf-8")
    finally:
        ranker.close()


if __name__ == "__main__":
    main()
