"""Offline audit for agent-response quality gaps.

This is intentionally a deterministic audit.  It does not call a provider,
the production API, or a database.  The cases exercise the current
answer-validator and the evidence gate used before an M2 goal is created.
The report separates *observed implementation behaviour* from the policy
expected by the workflow contracts, so a reviewer can decide whether a rule
should block, warn, or remain model-evaluated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.answer_validator import validate_answer  # noqa: E402
from app.goal_contract import proposal_evidence  # noqa: E402
from app.m4_prompts import MODULE_PROMPT as M4_PROMPT, RUNTIME_CONTRACT as M4_RUNTIME  # noqa: E402
from app.prompts import MODULE_PROMPTS  # noqa: E402


def fingerprint(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def message(role: str, content: str, position: int, ident: int = 1):
    return SimpleNamespace(
        role=role,
        content=content,
        position=position,
        id=ident,
        conversation_id=1,
    )


def validator_cases() -> list[dict]:
    """Cases where a normal delivery must be held by deterministic rules.

    ``expected`` is intentionally conservative: it only marks a case as
    ``block`` where the existing runtime contract already says the model must
    not make that claim.  These are not subjective counselling judgements.
    """

    no_plan = {"available": True, "current_module": "module_2", "plan_confirmed": False}
    no_goal = {"available": True, "current_module": "module_1", "plan_confirmed": False}
    return [
        {"id": "v-premature-plan", "module": "module_1", "reply": "从明天开始你应该每天跑步。", "workflow": no_goal,
         "expected": "block", "reason": "M1 禁止提前给活动/计划建议"},
        {"id": "v-unconfirmed-goal", "module": "module_2", "reply": "我们已经确定了这个目标，接下来开始执行。", "workflow": no_plan,
         "expected": "block", "reason": "未确认计划不得声称目标已确定"},
        {"id": "v-unconfirmed-completion", "module": "module_4", "reply": "你已经按照计划完成了，接下来继续就好。", "workflow": {"available": True, "current_module": "module_4", "plan_confirmed": False},
         "expected": "block", "reason": "未有当前执行证据不得声称完成"},
        {"id": "v-diagnosis-direct", "module": "module_1", "reply": "你患有抑郁症，需要服用药物。", "workflow": None,
         "expected": "block_or_review", "reason": "禁止诊断/用药建议"},
        {"id": "v-diagnosis-hedged", "module": "module_1", "reply": "你可能患有抑郁症，建议按抑郁症来处理。", "workflow": None,
         "expected": "block_or_review", "reason": "带可能的诊断仍是越权临床结论"},
        {"id": "v-panel-confirm", "module": "module_2", "reply": "请打开目标总览，确认并保存这个目标。", "workflow": no_plan,
         "expected": "block", "reason": "目标归属/确认只能在对话中完成"},
        {"id": "v-panel-readonly", "module": "module_2", "reply": "目标面板只用于回顾历史，目标确认在对话里完成。", "workflow": no_plan,
         "expected": "pass", "reason": "只读说明应允许"},
        {"id": "v-unsupported-completion-variant", "module": "module_4", "reply": "这次计划执行得很好，已经完成了安排。", "workflow": {"available": True, "current_module": "module_4", "plan_confirmed": False},
         "expected": "block", "reason": "同义完成宣称目前未被规则覆盖"},
        {"id": "v-uncommitted-transition-variant", "module": "module_2", "reply": "现在正式进入模块三，开始记录。", "workflow": {"available": True, "current_module": "module_2", "plan_confirmed": False},
         "expected": "block", "reason": "当前模块/计划状态不允许提前切换"},
    ]


def goal_evidence_cases() -> list[dict]:
    """M2 goal proposals that must never create a goal without user choice."""

    return [
        {
            "id": "g-assistant-suggestion-quoted",
            "messages": [message("assistant", "你可以站桩十分钟。", 1, 2), message("user", "助手说你可以站桩十分钟，我只是转述。", 2, 3)],
            "raw": {"goal_kind": "secondary", "selection_quote": "助手说你可以站桩十分钟", "activity_quote": "站桩十分钟"},
            "activity": "站桩十分钟", "expected": "reject", "reason": "助手建议/转述不是用户选择"},
        {
            "id": "g-past-behaviour",
            "messages": [message("user", "我做过散步，今天也只是想起它。", 1, 3)],
            "raw": {"goal_kind": "secondary", "selection_quote": "我做过散步", "activity_quote": "散步"},
            "activity": "散步", "expected": "reject", "reason": "过去行为/回忆不是新目标选择"},
        {
            "id": "g-explicit-negative",
            "messages": [message("user", "我选择不散步。", 1, 3)],
            "raw": {"goal_kind": "secondary", "selection_quote": "我选择不散步", "activity_quote": "不散步"},
            "activity": "不散步", "expected": "reject", "reason": "否定行为不应成为目标活动"},
        {
            "id": "g-consideration",
            "messages": [message("user", "我考虑每天散步，但还没决定。", 1, 3)],
            "raw": {"goal_kind": "secondary", "selection_quote": "我考虑每天散步，但还没决定", "activity_quote": "散步"},
            "activity": "散步", "expected": "reject", "reason": "考虑中/未决定应保持草稿"},
        {
            "id": "g-explicit-choice",
            "messages": [message("user", "我选择每天晚饭后散步。", 1, 3)],
            "raw": {"goal_kind": "secondary", "selection_quote": "我选择每天晚饭后散步", "activity_quote": "每天晚饭后散步"},
            "activity": "每天晚饭后散步", "expected": "accept", "reason": "用户明确选择具体活动"},
    ]


def run_validator_audit(repetitions: int) -> dict:
    rows = []
    for case in validator_cases():
        for iteration in range(repetitions):
            observed = validate_answer(
                reply=case["reply"],
                module=case["module"],
                evidence_ids=[],
                workflow=case["workflow"],
            )
            status = observed["status"]
            if case["expected"] == "pass":
                ok = status not in {"blocked"}
            elif case["expected"] == "block":
                ok = status == "blocked"
            else:
                ok = status in {"blocked", "review"}
            if not ok:
                rows.append({
                    "case_id": case["id"],
                    "iteration": iteration,
                    "observed_status": status,
                    "findings": observed["findings"],
                    "expected": case["expected"],
                    "reason": case["reason"],
                    "fingerprint": fingerprint(case["reply"]),
                })
    return {
        "cases": len(validator_cases()),
        "repetitions_per_case": repetitions,
        "executions": len(validator_cases()) * repetitions,
        "failures": rows,
        "failure_count": len(rows),
    }


def run_goal_audit(repetitions: int) -> dict:
    rows = []
    for case in goal_evidence_cases():
        for iteration in range(repetitions):
            observed = proposal_evidence(case["raw"], case["messages"], case["activity"])
            accepted = observed is not None
            expected = case["expected"]
            ok = accepted if expected == "accept" else not accepted
            if not ok:
                rows.append({
                    "case_id": case["id"],
                    "iteration": iteration,
                    "accepted": accepted,
                    "evidence": observed,
                    "expected": expected,
                    "reason": case["reason"],
                    "fingerprint": fingerprint(case["raw"]),
                })
    return {
        "cases": len(goal_evidence_cases()),
        "repetitions_per_case": repetitions,
        "executions": len(goal_evidence_cases()) * repetitions,
        "failures": rows,
        "failure_count": len(rows),
    }


def static_prompt_audit() -> dict:
    m3 = MODULE_PROMPTS["module_3"]
    findings = []
    # These strings are directly injected into the model system prompt.  They
    # are compared with the current UI contract and therefore are objective,
    # not a judgement about prose style.
    stale_ui = [
        ("stale_record_location", "聊天页面右上角的笔记本图标", "记录今日已移到左侧 navigator"),
        ("removed_history_entry", "我的每日记录", "入口已合并到记录今日的历史记录"),
        ("removed_not_applicable_option", "没有预定计划可选“不适用”", "该选项已删除"),
        ("old_mood_wording", "整体／平均心情", "当前文案为整体心情"),
    ]
    for code, needle, why in stale_ui:
        if needle in m3:
            findings.append({"code": code, "severity": "high", "why": why, "needle": needle})

    # M4 has two server/model instructions that cannot both be true for an
    # independently selected secondary goal.
    if "不负责：" in M4_PROMPT and "对次要目标进行独立、完整的复盘" in M4_PROMPT and "独立 secondary 目标被选中后同样享有完整复盘" in M4_RUNTIME:
        findings.append({"code": "m4_secondary_goal_contract_conflict", "severity": "high",
                         "why": "model prompt forbids independent secondary review while runtime contract grants it"})
    if "primary/secondary" not in MODULE_PROMPTS["module_2"] and "主要目标" not in MODULE_PROMPTS["module_2"]:
        findings.append({"code": "m2_goal_kind_not_explicit", "severity": "medium",
                         "why": "M2 prompt does not explain primary/secondary goal choice; extraction contract must carry it"})
    return {"finding_count": len(findings), "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=1000)
    parser.add_argument("--output", default=str(ROOT / ".test-tmp" / "response-quality-audit-20260918.json"))
    args = parser.parse_args()
    if args.repetitions < 1:
        raise SystemExit("repetitions must be >= 1")
    report = {
        "scope": "local deterministic only",
        "production_access": False,
        "provider_calls": 0,
        "validator_audit": run_validator_audit(args.repetitions),
        "goal_evidence_audit": run_goal_audit(args.repetitions),
        "static_prompt_audit": static_prompt_audit(),
        "limitations": [
            "Does not measure subjective counselling quality, empathy, or factual entailment.",
            "Does not call an LLM; model-specific failures need a blinded response set.",
            "A validator review status is observed as delivered because graph.py only blocks status=blocked.",
        ],
    }
    report["status"] = "findings" if (
        report["validator_audit"]["failure_count"]
        or report["goal_evidence_audit"]["failure_count"]
        or report["static_prompt_audit"]["finding_count"]
    ) else "clean"
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "status": report["status"],
        "validator_failures": report["validator_audit"]["failure_count"],
        "goal_evidence_failures": report["goal_evidence_audit"]["failure_count"],
        "prompt_findings": report["static_prompt_audit"]["finding_count"],
        "output": str(output),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
