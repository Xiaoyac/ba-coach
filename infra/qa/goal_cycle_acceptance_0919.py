"""Production DeepSeek dialogue QA using public authenticated APIs only.

No module override, forced confirmation, database access, or local data upload.
Artifacts are test-only, local and excluded from deployment.
"""
import json
from pathlib import Path
import sys
import time
import uuid
import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://bacoach.xyz"
OUT = Path(__file__).resolve().parents[2] / "work/goal-cycle-acceptance-0919"


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def api(s, method, path, **kwargs):
    r = s.request(method, BASE + path, timeout=(15, 120), **kwargs)
    r.raise_for_status()
    return r.json()


def compact(p):
    rt = p.get("runtime", {})
    d = p.get("draft") or {}
    return {"module": rt.get("current_module"), "flow": rt.get("flow_status"),
            "goal": rt.get("active_goal_id"), "cycle": rt.get("active_cycle_id"),
            "reason": rt.get("last_transition_reason"), "missing": p.get("missing_fields"),
            "can_confirm": p.get("can_confirm"), "draft": {k: d.get(k) for k in (
                "activity_content", "schedule_text", "scheduled_start_at", "location", "duration_minutes",
                "frequency_rule", "potential_barriers", "barrier_coping_plan", "negotiated_record_plan",
                "record_status", "review_decision") if k in d}}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    action = sys.argv[1]
    if action == "say" and (OUT / "pause.flag").exists():
        print("QA_AT_SAFE_TURN_BOUNDARY: waiting for scoped UI release", flush=True)
        while (OUT / "pause.flag").exists():
            time.sleep(1)
    s = requests.Session()
    if action == "init":
        assert not (OUT / "credentials.local-only.json").exists()
        suffix = uuid.uuid4().hex[:10]
        credentials = {"username": "qaGoal" + suffix, "password": "QaGoal!" + uuid.uuid4().hex}
        a = api(s, "POST", "/api/auth/register", json={**credentials,
            "email": "qagoal" + suffix + "@example.com", "nickname": "目标周期验收",
            "tag": str(int(suffix, 16) % 100000).zfill(5), "birth_date": "1995-06-15",
            "living_status": "和家人", "communication_preference": "温柔引导",
            "physical_condition": [], "behavior_taboo": []})
        write("credentials.local-only.json", {**credentials, "token": a["token"]})
        write("state.json", {"username": credentials["username"], "account": a.get("account"), "sessions": []})
        print(json.dumps({"created_test_account": credentials["username"]}, ensure_ascii=False), flush=True)
        return
    credentials = json.loads((OUT / "credentials.local-only.json").read_text(encoding="utf-8"))
    s.headers["Authorization"] = "Bearer " + credentials["token"]
    state = json.loads((OUT / "state.json").read_text(encoding="utf-8"))
    if action == "new":
        c = api(s, "POST", "/api/conversations")
        state["sessions"].append({"session_id": c["session_id"], "label": sys.argv[2], "turns": []})
        state["active"] = len(state["sessions"]) - 1
        write("state.json", state)
        print(json.dumps({"created": c["session_id"], "initial_module": c.get("next_module")}, ensure_ascii=False), flush=True)
        return
    scenario = state["sessions"][state["active"]]
    sid = scenario["session_id"]
    if action == "select":
        state["active"] = int(sys.argv[2])
        write("state.json", state)
        return
    if action == "show":
        p = api(s, "GET", "/api/program/" + sid)
        print(json.dumps(compact(p), ensure_ascii=False), flush=True)
        return
    if action == "say":
        text = sys.argv[2]
        started = time.perf_counter()
        row = {"user": text, "events": [], "reply": "", "first_content_ms": None,
               "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        with s.post(BASE + "/api/chat/stream", json={"session_id": sid, "provider": "deepseek",
                    "message": text, "generation_id": str(uuid.uuid4())}, stream=True, timeout=(15, 120)) as response:
            row["http_status"] = response.status_code
            response.raise_for_status()
            response.encoding = "utf-8"
            event = "message"
            for line in response.iter_lines(decode_unicode=True):
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    body = json.loads(line[5:])
                    row["events"].append({"event": event, "data": body,
                                          "elapsed_ms": round((time.perf_counter() - started) * 1000)})
                    if event == "delta":
                        if row["first_content_ms"] is None:
                            row["first_content_ms"] = round((time.perf_counter() - started) * 1000)
                        row["reply"] += body.get("text", "")
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        row["ok"] = bool(row["reply"] and any(e["event"] == "done" for e in row["events"])
                         and not any(e["event"] == "error" for e in row["events"]))
        scenario["turns"].append(row)
        write("state.json", state)
        print(json.dumps({"turn": len(scenario["turns"]), "reply": row["reply"], "ok": row["ok"],
                          "elapsed_ms": row["elapsed_ms"], "first_content_ms": row["first_content_ms"]}, ensure_ascii=False), flush=True)
        until = time.monotonic() + 75
        while True:
            detail = api(s, "GET", "/api/conversations/" + sid)
            p = api(s, "GET", "/api/program/" + sid)
            last = (detail.get("messages") or [{}])[-1]
            if (last.get("role") == "assistant" and last.get("routing_reasoning_content")) or time.monotonic() >= until or not row["ok"]:
                break
            time.sleep(2)
        row["detail"], row["program"] = detail, p
        row["settled_ms"] = round((time.perf_counter() - started) * 1000)
        row["overview"] = api(s, "GET", "/api/program/goals/overview")
        if p.get("runtime", {}).get("active_goal_id"):
            row["history"] = api(s, "GET", "/api/program/goals/" + p["runtime"]["active_goal_id"] + "/history")
        write("state.json", state)
        print(json.dumps({"state": compact(p), "next_module": detail.get("next_module"),
                          "settled_ms": row["settled_ms"], "router": last.get("routing_reasoning_content")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
