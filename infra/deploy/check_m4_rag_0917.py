"""Synthetic provider acceptance. No clinical writes or production user queries."""
import argparse
import asyncio
import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from time import perf_counter


async def main(args):
    logging.disable(logging.CRITICAL)
    if args.candidate_dir:
        for name in ("clinical_fields", "m4_contract", "clinical_extraction", "m4_prompts", "prompts"):
            spec = importlib.util.spec_from_file_location("app." + name, Path(args.candidate_dir) / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules["app." + name] = module
            spec.loader.exec_module(module)
    from app.config import get_settings
    from app.providers import get_provider
    from app.clinical_extraction import extract_module_record_detailed
    from app.clinical_fields import MODULE_FOUR
    from app.clinical_store import coerce
    from app.m4_contract import normalize, contract_for
    provider = get_provider()
    summaries = []
    try:
        for scenario in ("A", "B", "C"):
            facts = {"A": "昨天约定晚饭后散步十分钟。我饭后在小区走满了十分钟，开始有点低落，走完心情轻松了。没有遇到什么困难。",
                     "B": "昨天约定晚饭后散步十分钟，那个时间已经过去了。我还没开始，下大雨出不了门，这就是阻碍。一直坐在家里，后来仍很烦躁。",
                     "C": "昨天约定晚饭后散步十分钟，那个时间已经过去了。我在小区走满十分钟，开始低落，走完没有变开心，情绪没改善。没有遇到困难。"}[scenario]
            summary = {"A": "你饭后有点低落，仍在小区散步十分钟，随后轻松了；这次行动给你带来了积极反馈。",
                       "B": "下雨让你没法出门，散步没有开始，后来仍然烦躁；这次环境限制影响了行动。",
                       "C": "你开始时低落，完成了散步十分钟，之后情绪没有改善；行动发生了，但即时情绪效果还不明显。"}[scenario]
            education = "行动不一定等心情变好才开始，也不保证立刻开心。可以尝试后观察真实反馈，再决定怎样继续。"
            understanding = "我理解了，行动不保证立刻开心，可以先尝试观察再调整。我没有别的疑问。"
            decision = "我想调整原来的散步目标，去模块二讨论。" if scenario == "B" else "我没有需要处理的困难，就继续原来这个散步计划。"
            final = "这次" + ("下雨使散步未开始，ABC已经核对，你理解了行动与反馈的关系，决定回模块二调整原目标。" if scenario == "B" else "散步已完成，ABC已经核对，你理解了行动与反馈的关系，没有需要处理的困难，决定继续原计划。")
            turns = [("user", facts), ("assistant", summary), ("user", "这个分析符合我的经历，我确认。"),
                     ("assistant", education), ("user", understanding), ("assistant", "你希望接下来怎么安排？"),
                     ("user", decision), ("assistant", final)]
            transcript = "本次绑定目标：散步十分钟；以下全部是当前执行周期的模拟对话。\n" + "\n".join(role + "：" + body for role, body in turns)
            start = perf_counter()
            raw, completion = await asyncio.wait_for(extract_module_record_detailed(provider,
                module="module_4", transcript=transcript, max_tokens=4800), 60)
            data = coerce(MODULE_FOUR, raw)
            data["m4_contract"] = raw.get("m4_contract")
            messages = [SimpleNamespace(id=i+1, position=i+1, role=role, content=body) for i, (role, body) in enumerate(turns)]
            result = normalize(data, messages, session_id="synthetic", cycle_id="synthetic", assistant_message_id=8)
            contract = contract_for(result)
            item = {"case": scenario, "model": completion.model, "finish_reason": completion.finish_reason,
                    "scenario": result["scenario_type"], "execution_result": result["execution_result"],
                    "missing_fields": contract["missing_fields"], "chain_status": result["chain_confirmation_status"],
                    "confirmation_message_id": result["confirmation_message_id"], "usage": completion.usage,
                    "duration_ms": round((perf_counter()-start)*1000)}
            if args.debug:
                item["synthetic_extraction"] = raw
            summaries.append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
        if args.incremental:
            previous = None
            for cutoff in (2, 4, 6, 8):
                partial = turns[:cutoff]
                raw, completion = await asyncio.wait_for(extract_module_record_detailed(provider,
                    module="module_4", transcript="当前绑定目标：散步十分钟。\n" + "\n".join(role + "：" + body for role, body in partial), max_tokens=4800), 60)
                data = coerce(MODULE_FOUR, raw)
                data["m4_contract"] = raw.get("m4_contract")
                messages = [SimpleNamespace(id=i+1, position=i+1, role=role, content=body) for i, (role, body) in enumerate(partial)]
                result = normalize(data, messages, session_id="synthetic", cycle_id="synthetic", assistant_message_id=cutoff, existing=previous)
                contract = contract_for(result)
                print(json.dumps({"incremental_turns": cutoff, "chain_status": result["chain_confirmation_status"], "missing_fields": contract["missing_fields"],
                                  **({"synthetic_extraction": raw} if args.debug else {})}, ensure_ascii=False), flush=True)
                if cutoff == 2 and result["chain_confirmation_status"] == "confirmed":
                    raise RuntimeError("Premature confirmation")
                if cutoff == 8 and contract["missing_fields"]:
                    raise RuntimeError("Sequential completion failed")
                previous = result
        if any(x["scenario"] != x["case"] or x["missing_fields"] for x in summaries):
            raise RuntimeError("Synthetic M4 acceptance did not pass all gates")
    finally:
        if hasattr(provider, "_client"):
            await provider._client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--incremental", action="store_true")
    asyncio.run(main(parser.parse_args()))
