"""Synthetic M1 extraction smoke test; calls a model only with --live.

Never imports a DB/session, never creates accounts, conversations or records.
Not a clinical or statistical quality evaluation.
"""
import argparse
import asyncio
import json
from time import perf_counter
from app.clinical_extraction import extract_module_record_detailed
from app.clinical_fields import MODULE_ONE
from app.clinical_store import coerce
from app.m1_contract import snapshot
from app.providers import get_provider

EDUCATION = "情绪、精力和行动会互相影响。活动和反馈减少，有时会让困扰维持，这是一般模型，不是在替你下结论。BA 选择从可控制的行动端获得新反馈，但做了不保证立刻开心。我们采取尝试、观察、调整的方式，不是要求你靠意志力硬撑。你怎么理解这个方法？还有什么核心疑问？"
UNDERSTANDING = "我理解了，不是做了就开心，而是看看行动后的真实反馈，再调整。我没有其他疑问。"
PERSONAL = [
    ("user", "我希望改善下班后心情烦躁。昨天回家看到桌上没整理的东西，觉得心烦，我坐在沙发上，没有收拾；坐了半小时后还是心烦，没有明显变化。"),
    ("assistant", "你看到没整理的东西时心烦，随后坐了半小时、没有收拾，之后心烦没有明显变化。这是这一次行动与状态的关系，不代表一定是回避。这样整理符合吗？"),
    ("user", "符合，就是这样。"),
    ("assistant", "对此你有没有想到或尝试过缓解的方法？"),
    ("user", "没有，我还没想到或试过什么方法。"),
]
LOW = [("user", "我现在不想透露具体经历，也不想做个人分析。"),
       ("assistant", "可以，我们不追问私人经历。这会限制个性化，但可以先了解一般 BA 方法。")]


def turns(case):
    base = LOW if case == "low" else PERSONAL
    result = base + [("assistant", EDUCATION), ("user", UNDERSTANDING),
                     ("assistant", "你愿意接下来进入目标设定吗？"),
                     ("user", "我愿意进入目标设定。" if case != "no_consent" else "先不要，我还不想开始目标设定。")]
    if case == "correction":
        result += [("user", "刚才的总结不对，坐下后其实心烦缓解了，不是没有变化，请改掉之前的总结。"),
                   ("assistant", "谢谢纠正：你坐下半小时后心烦缓解了。我们先按这个实际结果核对理解，不沿用旧总结。")]
    else:
        result += [("assistant", "我们尊重你的选择，请以当前记录核对为准。")]
    return result


async def main(args):
    provider = get_provider(args.provider)
    messages = turns(args.case)
    start = perf_counter()
    raw, completion = await extract_module_record_detailed(provider, module="module_1",
        transcript="\n".join(f"{r}：{t}" for r, t in messages), max_tokens=4800)
    data = snapshot(raw, coerce(MODULE_ONE, raw), messages, "synthetic-m1-acceptance")
    contract = data["m1_contract"]
    ready = not contract["missing_fields"]
    expected = args.case in ("personal", "low")
    print(json.dumps({"case": args.case, "model": completion.model, "duration_ms": round((perf_counter()-start)*1000),
        "expected_ready": expected, "ready": ready, "passed": ready == expected,
        "path": contract["path"], "missing_fields": contract["missing_fields"],
        "finish_reason": completion.finish_reason, "usage": completion.usage,
        "summary_has_quote": bool(contract["evidence"].get("summary")),
        "approval": contract["user_approval_level"], "methods": data.get("attempted_relief_methods")}, ensure_ascii=False))
    if ready != expected:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--provider")
    parser.add_argument("--case", choices=["personal", "low", "no_consent", "correction"], default="personal")
    args = parser.parse_args()
    if not args.live:
        parser.error("No API call made. Explicit --live is required.")
    asyncio.run(main(args))
