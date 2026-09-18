"""Detect contradictory/stale prompt instructions that can cause bad replies.

This is a read-only local check.  It does not call a provider or inspect any
database.  A finding means two instructions are simultaneously present in the
compiled source and need an explicit precedence decision or cleanup.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def locations(path: Path, needle: str) -> list[int]:
    return [i for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if needle in line]


def main() -> None:
    m4 = ROOT / "backend/app/m4_prompts.py"
    prompts = ROOT / "backend/app/prompts.py"
    sidebar = ROOT / "frontend/components/ConversationSidebar.tsx"
    findings = []

    checks = [
        {
            "id": "m4_secondary_instruction_conflict",
            "file": m4,
            "old": "对次要目标进行独立、完整的复盘",
            "new": "独立 secondary 目标被选中后同样享有完整复盘",
            "description": "同一 M4 source 同时禁止和要求独立 secondary 复盘。",
        },
        {
            "id": "m3_daily_record_stale_entry",
            "file": prompts,
            "old": "点击聊天页面右上角的笔记本图标",
            "new": "记录今日",
            "new_file": sidebar,
            "description": "M3 运行提示仍指向旧的右上角入口；当前导航文案是记录今日。",
        },
        {
            "id": "m3_daily_record_stale_history",
            "file": prompts,
            "old": "侧栏“我的每日记录”",
            "new": "记录今日",
            "new_file": sidebar,
            "description": "M3 运行提示仍引用已移除/改名的历史入口。",
        },
        {
            "id": "m3_daily_record_stale_scale",
            "file": prompts,
            "old": "整体／平均心情",
            "new": "整体心情",
            "new_file": ROOT / "frontend/components/DailyAssessmentModal.tsx",
            "description": "M3 运行提示仍使用旧的整体/平均心情措辞。",
        },
    ]
    for check in checks:
        old_lines = locations(check["file"], check["old"])
        new_file = check.get("new_file", check["file"])
        new_lines = locations(new_file, check["new"])
        if old_lines and new_lines:
            findings.append({
                "id": check["id"],
                "file": str(check["file"].relative_to(ROOT)),
                "old_lines": old_lines,
                "new_file": str(new_file.relative_to(ROOT)),
                "new_lines": new_lines,
                "description": check["description"],
            })

    # Confirm the UI source of truth has the current navigation label.  This
    # keeps the prompt finding tied to an actual UI change, not a guess.
    sidebar_lines = locations(sidebar, "记录今日")
    ui_check = {"id": "ui_current_daily_record_entry", "file": str(sidebar.relative_to(ROOT)),
                "lines": sidebar_lines, "present": bool(sidebar_lines)}
    report = {
        "scope": "local static source check",
        "finding_count": len(findings),
        "findings": findings,
        "ui_source_check": ui_check,
        "status": "findings" if findings else "passed",
        "limitations": [
            "This finds textual contradictions/stale references only; it does not prove how a provider will resolve them.",
            "No model, network, database, production site, or user data was accessed.",
        ],
    }
    out = ROOT / ".test-tmp/prompt-consistency-audit-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "finding_count": len(findings),
                      "report": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
