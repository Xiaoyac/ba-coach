"""Summarize test-owned API evidence; do not access production data."""
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "work/goal-release-0919/candidate/backend"))
from app.dialogue_confirmation import affirmative, fingerprint, summary_present

sys.stdout.reconfigure(encoding="utf-8")
OUT = ROOT / "work/goal-cycle-acceptance-0919"
state = json.loads((OUT / "state.json").read_text(encoding="utf-8"))
rows, timing, first = [], [], []
diagnostics = []
for case in state["sessions"]:
    turns = case["turns"]
    if not turns:
        continue
    last = turns[-1].get("program", {})
    history = turns[-1].get("history", {})
    seen = {t.get("program", {}).get("runtime", {}).get("current_module") for t in turns}
    cycles = {c["id"]: c for c in history.get("cycles", [])}
    rows.append({"label": case["label"], "session": case["session_id"], "turns": len(turns),
        "goals": len({t.get("program", {}).get("runtime", {}).get("active_goal_id") for t in turns} - {None}),
        "m3_seen": "module_3" in seen, "m4_seen": "module_4" in seen,
        "completed_cycles": sum(c.get("status") == "completed" for c in cycles.values()),
        "final_module": last.get("runtime", {}).get("current_module"),
        "flow": last.get("runtime", {}).get("flow_status"),
        "goal_status": history.get("goal", {}).get("status"),
        "missing": last.get("missing_fields"), "ok": sum(t.get("ok", False) for t in turns)})
    for i, t in enumerate(turns):
        timing.append(t["elapsed_ms"])
        if t.get("first_content_ms") is not None:
            first.append(t["first_content_ms"])
        if i == 0 or not affirmative(t["user"]):
            continue
        p = t.get("program", {})
        module = p.get("runtime", {}).get("current_module")
        if module not in {"module_2", "module_3"} or not p.get("draft"):
            continue
        d = dict(p["draft"])
        if d.get("scheduled_start_at"):
            d["scheduled_start_at"] = datetime.fromisoformat(d["scheduled_start_at"])
        previous = turns[i - 1].get("program", {}).get("runtime", {}).get("memory", {}).get("dialogue_draft", {})
        diagnostics.append({"scenario": case["label"], "turn": i+1, "module": module,
            "affirmative": True, "same_fingerprint": previous.get("fingerprint") == fingerprint(d),
            "summary_present": summary_present(module, turns[i-1]["reply"], d),
            "can_confirm": p.get("can_confirm"), "missing": p.get("missing_fields")})
report = {"cases": rows, "turns": len(timing), "mean_reply_ms": round(statistics.mean(timing)) if timing else None,
          "max_reply_ms": max(timing, default=None), "mean_first_content_ms": round(statistics.mean(first)) if first else None,
          "over_60s": sum(t>60000 for t in timing), "confirmation_diagnostics": diagnostics}
(OUT / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
