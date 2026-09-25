"""Deterministic minimum shape of the existing M2 completion contract.

Semantic truth still requires user evidence; these checks do not infer it.
Explicit no-barrier discussion uses ["用户明确表示暂无障碍"] and a matching
plan (for example "无需额外应对"); unknown/untouched empty lists cannot confirm.
"""
import re


# DeepSeek occasionally emits a short canonical label for an obstacle and a
# second, longer explanation of the same obstacle. Requiring byte-identical
# labels makes a complete card fail closed (for example ``贪睡`` versus
# ``周日中午午睡容易睡过头``). Keep this normalization deliberately small:
# only known obstacle families can cover an elaboration; unrelated barriers
# still block creation.
_BARRIER_FAMILIES = {
    "sleep": ("贪睡", "午睡", "睡过头", "睡醒", "起不来", "赖床"),
    "forget": ("忘记", "忘了", "记不住", "记得"),
    "weather": ("下雨", "天气", "暴雨", "降温", "太热", "太冷"),
    "time": ("忙", "时间", "加班", "临时工作", "事情多"),
    "energy": ("累", "没精神", "疲惫", "精力"),
}


def _barrier_normalized(value):
    """Remove only conversational wrappers around an otherwise same barrier.

    The extractor may keep ``可能就是``/``吧`` in the user's barrier and omit
    them from the coping-plan key.  These wrappers carry no new obstacle
    meaning; broad fuzzy matching would be unsafe here, so keep this a small
    explicit whitelist.
    """
    text = str(value or "").strip()
    for prefix in ("可能就是", "可能是", "可能会", "就是"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text.rstrip("。！!？?,，；;吧呀啊呢 ")


def _barrier_family(value):
    text = str(value or "").strip()
    if not text:
        return None
    for family, tokens in _BARRIER_FAMILIES.items():
        if any(token in text for token in tokens):
            return family
    return None


def _barrier_is_covered(barrier, coping_keys):
    barrier_key = _barrier_normalized(barrier)
    normalized_keys = {_barrier_normalized(key) for key in coping_keys}
    if barrier in coping_keys or (barrier_key and any(
            barrier_key == key or barrier_key in key or key in barrier_key
            for key in normalized_keys if key)):
        return True
    family = _barrier_family(barrier)
    if family is None:
        return False
    return any(_barrier_family(key) == family for key in coping_keys)


def missing_plan_fields(plan):
    missing = []
    for field in ("activity_content", "schedule_text"):
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            missing.append(field)
    # Optional details remain optional. If supplied they must still have the
    # documented shape; absence must not become a mandatory coaching task.
    if plan.get("location") is not None and not isinstance(plan["location"], str):
        missing.append("location")
    if plan.get("duration_minutes") is not None and (
            type(plan["duration_minutes"]) is not int or not 1 <= plan["duration_minutes"] <= 1440):
        missing.append("duration_minutes")
    frequency = plan.get("frequency_rule")
    if frequency is not None and (not isinstance(frequency, dict) or frequency.get("schema_version") != 1 or not str(frequency.get("text") or "").strip()):
        missing.append("frequency_rule")
    rating = plan.get("difficulty_rating")
    if type(rating) is not int or not 0 <= rating <= 5:
        missing.append("difficulty_rating")
    evidence = plan.get("difficulty_evidence")
    rating_evidence = evidence.get("rating") if isinstance(evidence, dict) else None
    if (not isinstance(rating_evidence, dict) or rating_evidence.get("value") != rating
            or type(rating_evidence.get("message_id")) is not int
            or not isinstance(rating_evidence.get("quote"), str) or not rating_evidence["quote"].strip()):
        missing.append("difficulty_evidence")
    barriers, coping = plan.get("potential_barriers"), plan.get("barrier_coping_plan")
    if not isinstance(barriers, list) or not barriers or any(not isinstance(x, str) or not x.strip() for x in barriers):
        missing.append("potential_barriers")
    if not isinstance(coping, list) or not coping or any(not isinstance(x, dict) or not isinstance(x.get("barrier"), str) or not x["barrier"].strip() or not isinstance(x.get("plan"), str) or not x["plan"].strip() for x in coping):
        missing.append("barrier_coping_plan")
    elif isinstance(barriers, list) and any(
            not _barrier_is_covered(b, {x["barrier"] for x in coping}) for b in barriers):
        missing.append("barrier_coping_plan")
    return missing


def recover_one_time_frequency(plan, messages):
    """Fill a missing one-off frequency only from explicit user wording.

    A date such as ``今天`` is not enough to infer repetition.  The user must
    also say ``一次``/``一下``/``试试`` (or an equivalent one-off phrase), and
    the same date marker must appear in the stored schedule text.  This keeps
    the readiness repair source-bound while covering the common card wording
    ``就今天做一下试试看吧``.
    """
    if not isinstance(plan, dict) or isinstance(plan.get("frequency_rule"), dict):
        return plan
    schedule = plan.get("schedule_text")
    if not isinstance(schedule, str) or not schedule.strip():
        return plan
    patterns = (
        r"(?P<text>(?:就|先|只|这次|本次)?(?:今天|明天|后天|这三天|本周|这周)[^，,。！？!?\n]{0,16}(?:一次|一下|试试看|试试))",
        r"(?P<text>(?:就|先|只)?(?:做|试)(?:一次|一下|试试看|试试))",
    )
    anchors = ("今天", "明天", "后天", "这三天", "本周", "这周", "这次", "本次")
    for message in messages or ():
        if getattr(message, "role", None) != "user":
            continue
        body = (getattr(message, "content", "") or "").strip()
        for pattern in patterns:
            match = re.search(pattern, body)
            if not match:
                continue
            phrase = match.group("text").strip(" ，,。！？!? ")
            if not phrase:
                continue
            if not any(anchor in body and anchor in schedule for anchor in anchors):
                # The second pattern has no date of its own.  It is safe only
                # when the schedule itself explicitly says this is one-off.
                if not any(token in schedule for token in ("一次", "一下", "试试", "试试看")):
                    continue
            return {**plan, "frequency_rule": {"schema_version": 1, "text": phrase}}
    return plan
