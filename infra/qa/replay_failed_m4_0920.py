"""Replay M4 review turns for accounts that stalled before the M4 durability fix."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import full_cycle_acceptance_0920 as cycle


def replay(actor: str) -> dict:
    client = cycle.Client(actor)
    path = client.folder / "01_散步.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    turns = cycle.templates(cycle.CASES[0], canonical=False)["module_4"]
    for text in turns:
        program = client.api("GET", "/api/program/" + data["session_id"])
        runtime = program.get("runtime") or {}
        if runtime.get("flow_status") in {"completed", "paused"}:
            break
        if runtime.get("current_module") != "module_4":
            continue
        client.say(data, text)
    program = client.api("GET", "/api/program/" + data["session_id"])
    runtime = program.get("runtime") or {}
    return {"actor": actor, "module": runtime.get("current_module"),
            "flow": runtime.get("flow_status"), "goal_id": runtime.get("active_goal_id"),
            "cycle_id": runtime.get("active_cycle_id")}


def main() -> None:
    actors = ["DeployP02", "DeployP04", "DeployP05", "DeployP10"]
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(replay, actor) for actor in actors]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if any(result["flow"] != "completed" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
