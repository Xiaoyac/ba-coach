"""Compact evidence summary; never changes judgments or retrieval settings."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/"evals/reports/rag0917"


def main():
    output={}
    for name in ("dev","holdout","stress","regression_blind_v1","fresh_v2"):
        path=ROOT/f"cascade_{name}.json"
        report=json.loads(path.read_text(encoding="utf-8"))
        before,after=report["baseline"],report["catalog"]
        improvements,regressions=[],[]
        for old,new in zip(before["cases"],after["cases"]):
            key="correct_empty" if old["expect_empty"] else "hit_at_k"
            if new["metrics"][key]>old["metrics"][key]:
                improvements.append(old["id"])
            if new["metrics"][key]<old["metrics"][key]:
                regressions.append({"id":old["id"],"query":old["query"],"old_hits":old["hits"],"new_hits":new["hits"]})
        calls=[stage for trace in report["trace"].values() for stage in (trace["plan"],trace["judgment"]) if stage]
        output[name]={"baseline":before["summary"],"catalog":after["summary"],"delta":report["delta"],
            "hit_or_empty_improvements":improvements,"hit_or_empty_regressions":regressions,
            "logical_model_calls":len(calls),"fast_paths":sum(t["fast_path"] for t in report["trace"].values()),
            "prompt_tokens":sum(c.get("usage",{}).get("prompt_tokens",0) for c in calls),
            "completion_tokens":sum(c.get("usage",{}).get("completion_tokens",0) for c in calls),
            "provider_errors":report["errors"]}
    path=ROOT/"summary.json"
    path.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    for name,result in output.items():
        print(json.dumps({"dataset":name,**result},ensure_ascii=False))


if __name__=="__main__":
    main()
