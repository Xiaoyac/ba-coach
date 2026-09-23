"""Recover four pre-fix acceptance conversations with an explicit repeated decision."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import full_cycle_acceptance_0920 as cycle


TEXT = "我确认这次复盘，我决定结束这个目标，不再开启下一周期，请提交本轮。"


def recover(actor: str) -> dict:
    client = cycle.Client(actor)
    path = client.folder / "01_散步.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    row = client.say(data, TEXT)
    program = row.get("program") or client.api("GET", "/api/program/" + data["session_id"])
    runtime = program.get("runtime") or {}
    return {
        "actor": actor,
        "ok": row.get("ok"),
        "module": runtime.get("current_module"),
        "flow": runtime.get("flow_status"),
        "reply": row.get("reply", "")[:160],
    }


def main() -> None:
    actors = ["DeployP02", "DeployP04", "DeployP05", "DeployP10"]
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(recover, actor) for actor in actors]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if any(result["flow"] != "completed" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
