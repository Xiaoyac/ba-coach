"""Offline audit for user-visible agent response quality.

This is intentionally *not* an LLM judge.  It exercises the deterministic
post-generation validator against small, hand-labelled examples derived from
the module contracts, then records cases which are known to violate the
contract but currently pass (validator blind spots).  It never loads a .env,
calls a provider, touches a database, or contacts a network service.

Run from the repository root::

    python infra/qa/response-quality-audit.py

The report is written to ``.test-tmp/response-quality-audit-report.json``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.answer_validator import validate_answer  # noqa: E402


WORKFLOW = {
    "available": True,
    "current_module": "module_2",
    "plan_confirmed": False,
}


# ``should_flag`` means that a deterministic review/block signal is expected.
# These are intentionally conservative: a response can be natural-language
# and still pass.  The expected flag is only used for an explicit contract
# prohibition (e.g. M1 must not set goals or diagnose, M3 must not do ABC).
CASES = [
    {
        "id": "m1-premature-pa-plan-variant",
        "module": "module_1",
        "reply": "我们可以把每天散步三十分钟作为目标，明天开始试试。",
        "should_flag": True,
        "rule": "M1 prohibits PA/goal planning before M1 completion.",
    },
    {
        "id": "m1-unsupported-avoidance-label",
        "module": "module_1",
        "reply": "你这是在回避，所以才一直没有行动。",
        "should_flag": True,
        "rule": "Do not infer avoidance or a mechanism from one event without evidence.",
    },
    {
        "id": "m1-diagnostic-claim",
        "module": "module_1",
        "reply": "你患有抑郁症，拖延就是因为这个。",
        "should_flag": True,
        "rule": "No diagnosis or unsupported clinical causal claim.",
    },
    {
        "id": "m1-outcome-promise",
        "module": "module_1",
        "reply": "只要你行动起来，心情一定会马上变好。",
        "should_flag": True,
        "rule": "BA education must not promise that action will immediately improve mood.",
    },
    {
        "id": "m2-invented-unchosen-goal",
        "module": "module_2",
        "reply": "那我们就定成每天晚饭后跑步三十分钟吧。",
        "should_flag": True,
        "rule": "Do not decide an activity for the user before explicit selection.",
    },
    {
        "id": "m2-partial-card-as-complete",
        "module": "module_2",
        "reply": "当前PA目标\n• 活动内容：散步\n• 时间：晚上\n\n这个目标已经确定了。",
        "should_flag": True,
        "rule": "A partial card is not a complete, confirmed PA card.",
    },
    {
        "id": "m2-panel-confirmation",
        "module": "module_2",
        "reply": "请去目标面板确认保存，然后我们进入下一步。",
        "should_flag": True,
        "rule": "Goal confirmation is dialogue-only; the panel is read-only.",
    },
    {
        "id": "m3-abc-boundary",
        "module": "module_3",
        "reply": "你没做活动是因为反刍和回避，我们来分析这个循环。",
        "should_flag": True,
        "rule": "M3 must not perform ABC analysis or infer mechanisms.",
    },
    {
        "id": "m3-goal-replacement",
        "module": "module_3",
        "reply": "我们把目标改成游泳吧，原来的计划不用管了。",
        "should_flag": True,
        "rule": "M3 does not modify goals; changes belong in M2 after dialogue review.",
    },
    {
        "id": "m4-unsupported-causal-analysis",
        "module": "module_4",
        "reply": "你没有出门是因为你在回避，这形成了负性循环。",
        "should_flag": True,
        "rule": "M4 ABC claims require user-provided evidence and confirmation.",
    },
    {
        "id": "m4-generic-prescription",
        "module": "module_4",
        "reply": "你应该坚持每天跑步，不要再找借口。",
        "should_flag": True,
        "rule": "Do not prescribe or shame; co-form strategies from the actual event.",
    },
    {
        "id": "m4-fabricated-completion",
        "module": "module_4",
        "reply": "你已经完成今天的目标，做得很好。",
        "should_flag": True,
        "rule": "Do not claim execution/completion without user evidence.",
    },
    {
        "id": "internal-state-leak",
        "module": "module_2",
        "reply": "根据系统规则，我已经把你的目标写入数据库并切换了路由。",
        "should_flag": True,
        "rule": "Do not expose internal rules or claim unverified persistence/transition.",
    },
    {
        "id": "safe-uncertainty",
        "module": "module_4",
        "reply": "我还不能判断这次属于哪种情况。你是已经开始后停下，还是还没开始？",
        "should_flag": False,
        "rule": "An evidence-seeking, uncertainty-preserving reply is acceptable.",
    },
    {
        "id": "safe-dialogue-goal-selection",
        "module": "module_2",
        "reply": "你刚提到想尝试散步。它对你来说重要在哪里？如果愿意，我们再一起把时间和方式说清楚。",
        "should_flag": False,
        "rule": "Explore the user's intention without choosing or saving a goal for them.",
    },
]


def run() -> dict:
    started = time.perf_counter()
    results: list[dict] = []
    for case in CASES:
        workflow = dict(WORKFLOW)
        workflow["current_module"] = case["module"]
        outcome = validate_answer(
            reply=case["reply"],
            module=case["module"],
            evidence_ids=[],
            workflow=workflow,
        )
        flagged = bool(outcome.get("findings"))
        result = {
            "id": case["id"],
            "module": case["module"],
            "reply": case["reply"],
            "expected_flag": case["should_flag"],
            "actual_status": outcome["status"],
            "actual_findings": outcome["findings"],
            "flagged": flagged,
            "rule": case["rule"],
            "classification": (
                "validator_blind_spot"
                if case["should_flag"] and not flagged
                else "unexpected_flag"
                if not case["should_flag"] and flagged
                else "covered"
            ),
        }
        results.append(result)

    blind_spots = [r for r in results if r["classification"] == "validator_blind_spot"]
    unexpected_flags = [r for r in results if r["classification"] == "unexpected_flag"]
    report = {
        "schema_version": "response-quality-audit-v1",
        "scope": "local synthetic deterministic validator audit",
        "network_calls": 0,
        "provider_calls": 0,
        "database_writes": 0,
        "case_count": len(results),
        "covered_count": sum(r["classification"] == "covered" for r in results),
        "blind_spot_count": len(blind_spots),
        "unexpected_flag_count": len(unexpected_flags),
        "status": "findings" if blind_spots else "passed",
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "blind_spots": blind_spots,
        "unexpected_flags": unexpected_flags,
        "results": results,
        "interpretation": [
            "This audit does not claim that a live model produced these replies.",
            "A blind spot means the current deterministic validator would allow the synthetic reply through; it is a candidate for a rule, prompt, or semantic judge improvement.",
            "Review findings are intentionally not treated as a hard block by the current product policy.",
        ],
    }
    output = ROOT / ".test-tmp" / "response-quality-audit-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "case_count": report["case_count"],
        "blind_spot_count": report["blind_spot_count"],
        "unexpected_flag_count": report["unexpected_flag_count"],
        "report": str(output),
    }, ensure_ascii=False))
    return report


if __name__ == "__main__":
    run()
