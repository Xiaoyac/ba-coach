"""Deterministic minimum shape of the existing M2 completion contract.

Semantic truth still requires user evidence; these checks do not infer it.
Explicit no-barrier discussion uses ["用户明确表示暂无障碍"] and a matching
plan (for example "无需额外应对"); unknown/untouched empty lists cannot confirm.
"""
def missing_plan_fields(plan):
    missing = []
    for field in ("activity_content", "schedule_text", "location"):
        if not isinstance(plan[field], str) or not plan[field].strip():
            missing.append(field)
    if not isinstance(plan["duration_minutes"], int) or not 1 <= plan["duration_minutes"] <= 1440:
        missing.append("duration_minutes")
    frequency = plan["frequency_rule"]
    if not isinstance(frequency, dict) or frequency.get("schema_version") != 1 or not str(frequency.get("text") or "").strip():
        missing.append("frequency_rule")
    barriers, coping = plan["potential_barriers"], plan["barrier_coping_plan"]
    if not isinstance(barriers, list) or not barriers or any(not isinstance(x, str) or not x.strip() for x in barriers):
        missing.append("potential_barriers")
    if not isinstance(coping, list) or not coping or any(not isinstance(x, dict) or not isinstance(x.get("barrier"), str) or not x["barrier"].strip() or not isinstance(x.get("plan"), str) or not x["plan"].strip() for x in coping):
        missing.append("barrier_coping_plan")
    elif isinstance(barriers, list) and any(b not in {x["barrier"] for x in coping} for b in barriers):
        missing.append("barrier_coping_plan")
    return missing
