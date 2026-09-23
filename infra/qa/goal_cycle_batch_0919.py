"""Nine additional, bounded public-API goal scenarios; no forced progression.

Scripted users proactively supply facts. Results are NOT unassisted usability
claims. Every module comes from the server; blocked cases stay blocked.
"""
import json
from pathlib import Path
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "work/goal-cycle-acceptance-0919"
CLIENT = Path(__file__).with_name("goal_cycle_acceptance_0919.py")
CASES = [
    ("02_伸展完成并结束", "站立伸展", "客厅", 5, "工作忙", "睡前站立伸展5分钟", "end", "complete"),
    ("03_走廊步行部分完成", "走廊步行", "办公室走廊", 10, "临时加班", "先走3分钟", "end", "partial"),
    ("04_整理房间未完成", "整理房间", "卧室", 10, "太累", "先整理2分钟", "pause", "not_started"),
    ("05_跳舞情绪未改善", "跟音乐跳舞", "客厅", 5, "没兴致", "先跳1分钟", "end", "no_improvement"),
    ("06_站桩继续原周期", "站桩", "阳台", 5, "太忙", "先站2分钟", "continue", "complete"),
    ("07_楼下散步调整计划", "楼下散步", "楼下花园", 10, "下雨", "在楼道走5分钟", "adjust", "partial"),
    ("08_整理书架替代活动", "整理书架", "书房", 10, "疲惫", "先整理2分钟", "end", "alternative"),
    ("09_阳台浇花确认改时间", "站着浇花", "阳台", 5, "忘记", "晚饭后看看花盆", "end", "complete"),
    ("10_原地踏步独立目标", "原地踏步", "客厅", 5, "工作忙", "先踏步1分钟", "end", "complete"),
]


def invoke(*args):
    subprocess.run([sys.executable, str(CLIENT), *args], cwd=ROOT, check=True)


def current():
    state = json.loads((OUT / "state.json").read_text(encoding="utf-8"))
    return state, state["sessions"][state["active"]]


def run_case(case):
    label, activity, location, duration, barrier, coping, decision, result = case
    invoke("new", label)
    plan = (f"我理解PA包括日常身体活动，不只是运动。我选择{activity}作为独立的小目标，"
            f"想让身体多活动一点。开始时间2026年9月19日21:00，在{location}做{duration}分钟，"
            f"每天一次，先试三天。难度大概2分，我能做到，有场地，不需要同伴。"
            f"可能遇到{barrier}，应对办法是{coping}。请帮我整理目标卡片让我确认。")
    m2 = [plan, "好，请把活动、日期时间、地点、时长、频率、障碍和应对完整列成目标卡片，并问我是否确认。",
          "确认，就按这个计划试试。",
          "我已经同意了，不过目前仍在目标设定。请把完整卡片重新列出，包含开始日期和时间，问我是否确认。",
          "确认，就按这个计划试试。"]
    if label.startswith("09"):
        m2.insert(2, "我想把开始时间改成2026年9月19日21:30，其他都不变，请更新卡片让我重新确认。")
    m3 = ["我想每天晚上做完后，在左侧记录今日填写活动时间、活动内容和做完后的心情，心情按0到5记录。其他感受有空再填；困难可以回来聊天。请整理这个记录方式让我确认。",
          "我同意这样记录。", "请完整重述我们商量的记录方式，并问我是否确认。", "我同意这样记录。"]
    feedback = {
        "complete": f"刚才我完成了{activity}{duration}分钟，现在这次执行已经结束了。开始前有点懒，但没有实际障碍，还是开始做了。做的时候慢慢放松，做完心情从2分到3分，身体舒展一些。",
        "partial": f"这次执行时间已经过去了。我做了{activity}3分钟就停了，计划是{duration}分钟，算部分完成。开始前因为{barrier}犹豫，想着先做一点也好。做完心情从2分到3分，身体轻松一些。",
        "not_started": f"今天约好的时间已经过去了。我没有开始{activity}，实际0分钟。开始前太累，想着以后再做，就躺下刷手机了；当时松口气，后来有点失落，心情仍是2分。",
        "no_improvement": f"这次执行时间已经过去。我完整做了{activity}{duration}分钟，开始前没有障碍。做完身体舒展些，但心情还是2分，没有改善。我想了解这是不是没用。",
        "alternative": f"这次执行时间已经过去了。我没做{activity}，因为疲惫觉得整理很麻烦，所以原计划实际0分钟。不过我另外出门散步了5分钟，心情从2分到3分。散步是临时做的，不是原来的目标。",
    }[result]
    decision_text = {"end": "我决定结束这个目标，这一轮就到这里，不再开启下一周期。",
                     "pause": "我决定先暂停这个目标，保留历史，不是彻底结束。",
                     "continue": "我决定继续原来的目标和原计划，进入下一执行周期，不新建目标。",
                     "adjust": "我决定调整原来的目标，把每次时长改为5分钟，重新商量计划，不创建另一个目标。"}[decision]
    m4 = [feedback, "对，你整理的经历和我的体验一致，我确认这次事件分析。",
          "我理解了，身体活动和心情相互影响，不能保证做完立即开心，应该先行动、观察反馈再调整，而不是硬撑。我没有其他疑问。" + decision_text,
          "这次的理解我已经清楚了，不需要再重复问我是否理解。" + decision_text + "请总结这次复盘。",
          "我确认前面的经历总结，也理解行动后观察再调整。" + decision_text,
          "对，这个总结准确。" + decision_text]
    counts = {"module_2": 0, "module_3": 0, "module_4": 0}
    templates = {"module_2": m2, "module_3": m3, "module_4": m4}
    original_cycle = None
    blocked = None
    for _ in range(19):
        state, scenario = current()
        p = scenario["turns"][-1].get("program", {}) if scenario["turns"] else {}
        rt = p.get("runtime", {})
        module = rt.get("current_module", "module_2")
        if rt.get("active_cycle_id") and original_cycle is None:
            original_cycle = rt["active_cycle_id"]
        history = scenario["turns"][-1].get("history", {}) if scenario["turns"] else {}
        if any(c.get("status") == "completed" for c in history.get("cycles", [])):
            blocked = None
            break
        if module not in templates:
            blocked = "unexpected_module:" + module
            break
        if counts[module] >= len(templates[module]):
            blocked = "bounded_stall:" + module
            break
        msg = templates[module][counts[module]]
        counts[module] += 1
        invoke("say", msg)
    state, scenario = current()
    scenario["acceptance"] = {"intended_decision": decision, "result": result, "attempts": counts,
                              "blocked": blocked, "original_cycle": original_cycle}
    (OUT / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"SCENARIO_FINISHED": label, **scenario["acceptance"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    for case in CASES[start:]:
        run_case(case)
