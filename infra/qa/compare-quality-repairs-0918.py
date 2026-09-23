"""Compare pinned pre-fix rules with current rules on unchanged synthetic cases.

Local only: read two known Python modules from Git, evaluate functions in memory.
No checkout/reset, .env loading, provider calls, or database connections.
These cases drove the fix and must not be described as holdout evaluation.
"""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
BASELINE = "75821097413175a6fc90e78ed8812564d8e2f165"

from app.answer_validator import validate_answer
from app.goal_contract import proposal_evidence


def baseline_function(module, function):
    path = f"backend/app/{module}.py"
    source = subprocess.check_output(["git", "show", f"{BASELINE}:{path}"], cwd=ROOT).decode("utf-8")
    namespace = {"__name__": f"app._baseline_{module}", "__package__": "app"}
    exec(compile(source, f"{BASELINE}:{path}", "exec"), namespace)
    return namespace[function]


def main():
    cases = runpy.run_path(str(ROOT / "infra/qa/response-quality-audit.py"))["CASES"]
    goals = runpy.run_path(str(ROOT / "infra/qa/response-quality-audit-0918.py"))["goal_evidence_cases"]()
    report = {"baseline_commit": BASELINE, "scope": "fixed synthetic regression cases, NOT holdout",
              "provider_calls": 0, "database_connections": 0, "reply_case_count": len(cases),
              "goal_case_count": len(goals), "results": {}}
    for name, validator, gate in (
        ("before", baseline_function("answer_validator", "validate_answer"), baseline_function("goal_contract", "proposal_evidence")),
        ("after", validate_answer, proposal_evidence),
    ):
        replies, goal_rows, timings = [], [], []
        for case in cases:
            result = validator(reply=case["reply"], module=case["module"], evidence_ids=[], workflow={
                "available": True, "current_module": case["module"], "plan_confirmed": False})
            replies.append({"id": case["id"], "should_block": case["should_flag"], "status": result["status"]})
        for case in goals:
            goal_rows.append({"id": case["id"], "expected": case["expected"],
                              "accepted": gate(case["raw"], case["messages"], case["activity"]) is not None})
        # Local rule time only; NOT response latency, throughput, or live model quality.
        for _ in range(200):
            for case in cases:
                timings.append(validator(reply=case["reply"], module=case["module"], evidence_ids=[],
                    workflow={"available": True, "current_module": case["module"], "plan_confirmed": False})["duration_ms"])
        timings.sort()
        report["results"][name] = {
            "bad_reply_count": sum(r["should_block"] for r in replies),
            "bad_replies_blocked": sum(r["should_block"] and r["status"] == "blocked" for r in replies),
            "bad_replies_review_only": sum(r["should_block"] and r["status"] == "review" for r in replies),
            "bad_replies_passed": sum(r["should_block"] and r["status"] == "passed" for r in replies),
            "good_reply_false_blocks": sum(not r["should_block"] and r["status"] == "blocked" for r in replies),
            "goal_false_accepts": sum(r["expected"] == "reject" and r["accepted"] for r in goal_rows),
            "goal_false_rejects": sum(r["expected"] == "accept" and not r["accepted"] for r in goal_rows),
            "local_rule_ms": {"samples": len(timings), "median": statistics.median(timings),
                              "p95": timings[int(len(timings) * .95) - 1]},
            "replies": replies, "goals": goal_rows,
        }
    out = ROOT / ".test-tmp/quality-repair-comparison-20260918.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: {k: v for k, v in result.items() if k not in {"replies", "goals"}}
                      for name, result in report["results"].items()}, ensure_ascii=False))
    after = report["results"]["after"]
    return int(after["bad_replies_blocked"] != after["bad_reply_count"] or after["good_reply_false_blocks"]
               or after["goal_false_accepts"] or after["goal_false_rejects"])


if __name__ == "__main__":
    raise SystemExit(main())
