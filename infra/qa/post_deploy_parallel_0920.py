"""Run ten independent post-deploy full-cycle acceptance cases in parallel."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys

import full_cycle_acceptance_0920 as cycle


def run(actor: str) -> dict:
    cycle.run_actor(actor, count=1, canonical=False)
    path = cycle.OUT / actor / "01_散步.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "actor": actor,
        "outcome": data.get("result", {}).get("outcome"),
        "goal_id": data.get("result", {}).get("goal_id"),
        "cycle_id": data.get("result", {}).get("cycle_id"),
        "attempts": data.get("result", {}).get("attempts"),
    }


def main() -> None:
    actors = [f"DeployP{index:02d}" for index in range(1, 11)]
    results = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(run, actor) for actor in actors]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    results.sort(key=lambda row: row["actor"])
    cycle.save(cycle.OUT / "post-deploy-parallel-summary.json", results)
    completed = sum(row["outcome"] == "cycle_completed" for row in results)
    print(json.dumps({"completed": completed, "total": len(results)}, ensure_ascii=False))
    if completed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
