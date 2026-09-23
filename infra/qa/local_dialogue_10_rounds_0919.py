"""Local ten-turn dialogue acceptance run.

This drives the real LangGraph through ordinary turn calls and the real
validator/contracts, but uses a deterministic local Provider because this
checkout has no provider credentials. It never contacts the public site or a
production database. Module changes are applied by the scripted router between
turns so the ten-turn transcript can cover M1 -> M2 -> M3 -> M4 repeatably.
"""
from __future__ import annotations

import asyncio
import json
import runpy
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.answer_validator import validate_answer
from app.config import Settings
from app.goal_contract import proposal_evidence
from app.graph import get_graph
from app.graph.state import GraphContext
from app.providers.base import Completion, LLMProvider, StreamDelta
from app.retrieval import StubKnowledgeBase
from app.schemas import Message
from app.session import InMemorySessionStore
from app.workflow_contract import MODULE_STEP_KEYS


class LocalDialogueProvider(LLMProvider):
    name = "local-dialogue-script"
    model = "local-dialogue-script-v1"

    def __init__(self) -> None:
        self.turn = 0
        self.systems: list[str] = []

    @staticmethod
    def _module(system) -> str:
        text = "\n".join(getattr(part, "text", str(part)) for part in system) if not isinstance(system, str) else system
        for number in range(1, 5):
            if f"reply_module: module_{number}" in text:
                return f"module_{number}"
        return "module_1"

    async def complete(self, *, system, messages: list[Message]) -> Completion:
        self.systems.append("\n".join(getattr(part, "text", str(part)) for part in system) if not isinstance(system, str) else system)
        module = self._module(system)
        replies = {
            "module_1": "我们先把这次经历和它带来的感受说清楚，再一起看看下一步。",
            "module_2": "我们可以把你愿意尝试的活动具体化，按你的节奏讨论时间和难度。",
            "module_3": "我们约定用简短的每日记录回看实际发生的活动和感受。",
            "module_4": "我们先回看这次真实经历，再由你决定继续、调整还是暂停。",
        }
        self.turn += 1
        return Completion(text=replies[module], model=self.model,
                          reasoning_content=f"本地脚本思考：第{self.turn}轮，当前{module}。",
                          usage={"input_tokens": 20, "output_tokens": 18}, finish_reason="stop",
                          request_id=f"local-{self.turn}")

    async def stream(self, *, system, messages: list[Message]):
        result = await self.complete(system=system, messages=messages)
        yield StreamDelta(kind="reasoning", text=result.reasoning_content)
        yield StreamDelta(kind="content", text=result.text)
        yield StreamDelta(kind="usage", usage=result.usage, finish_reason="stop", request_id=result.request_id)

    async def classify(self, *, system: str, user: str, allowed, default: str) -> str:
        return default

    async def route(self, *, system: str, user: str, max_tokens=None) -> str:
        # Risk and retrieval helpers receive a conservative empty object.
        return "{}"


def _db_messages(store: InMemorySessionStore, session_id: str):
    session = store._sessions[session_id]  # local harness only
    return [SimpleNamespace(id=i + 1, position=i, role=m.role, content=m.content, conversation_id=1)
            for i, m in enumerate(session.messages)]


async def main() -> int:
    provider = LocalDialogueProvider()
    store = InMemorySessionStore(ttl_seconds=3600, max_messages=80)
    settings = Settings(_env_file=None, database_schema_version="legacy",
                        knowledge_intent_gate_enabled=False,
                        knowledge_mediator_enabled=False,
                        answer_validator_enabled=True, risk_gate_enabled=False)
    context = GraphContext(provider=provider, router_provider=provider, store=store,
                           knowledge_base=StubKnowledgeBase(), settings=settings, stream=False)
    session_id = (await store.get_or_create(None)).session_id

    turns = [
        ("module_1", "最近下班后看到学习资料就焦虑，很难开始。"),
        ("module_1", "昨天我躺下刷手机，没有开始学习，后来更烦躁。"),
        ("module_1", "这个总结基本符合，我以前试过列小清单。"),
        ("module_1", "我理解了，也愿意进入目标设定。"),
        ("module_2", "我想找回生活节奏，先讨论一个可以做到的小行动。"),
        ("module_2", "我选择在小区平路慢走五分钟，晚饭后进行。"),
        ("module_2", "好，就按这个计划试试。"),
        ("module_3", "我愿意用每日记录记下活动时间、内容和做完后的心情。"),
        ("module_4", "昨天晚饭后我慢走了五分钟，做完后感觉轻松一点。"),
        ("module_4", "这次复盘后我决定继续原计划。"),
    ]
    rows = []
    for index, (module, user_text) in enumerate(turns, 1):
        started = time.perf_counter()
        result = await get_graph().ainvoke({
            "session_id": session_id, "user_input": user_text,
            "forced_module": module, "metadata": {}, "subject_id": None,
        }, context=context)
        elapsed = round((time.perf_counter() - started) * 1000)
        reply = str(result.get("final_response") or "")
        validation = validate_answer(reply=reply, module=module, evidence_ids=[], workflow={
            "available": True, "current_module": module, "plan_confirmed": module in {"module_3", "module_4"}})
        rows.append({"round": index, "input": user_text, "reply": reply,
                     "expected_module": module, "actual_module": result.get("extracted_intent"),
                     "next_module": result.get("next_module"), "routing_pending": result.get("routing_pending"),
                     "validator_status": validation["status"], "elapsed_ms": elapsed})
        # In production this is the post-hoc Router commit. Applying the
        # scripted decision here lets the following ordinary turn read it back.
        if index in {4, 7, 8, 10}:
            next_module = {4: "module_2", 7: "module_3", 8: "module_4", 10: "module_4"}[index]
            await store.set_module(session_id, next_module)

    messages = _db_messages(store, session_id)
    # Goal evidence is evaluated at the M2 selection turn.  Later M3/M4
    # messages are deliberately not allowed to replace that source turn.
    goal = proposal_evidence({
        "goal_kind": "primary", "long_term_direction": "找回生活节奏",
        "selection_quote": "我选择在小区平路慢走五分钟，晚饭后进行",
        "activity_quote": "慢走五分钟", "direction_quote": "找回生活节奏",
    }, messages[:12], "小区平路慢走五分钟")

    # Reuse the project's valid M4 fixture to verify the final contract gate.
    stress = runpy.run_path(str(ROOT / "infra" / "qa" / "m1-m4-stress-1000.py"))
    m4_data, m4_messages = stress["m4_fixture"](919)
    m4_result = stress["normalize_m4"](m4_data, m4_messages, session_id=session_id,
                                         cycle_id="local-cycle", assistant_message_id=92900)
    m4_steps = m4_result["phase_c"]["_m4_contract"]["completed_steps"]
    m4_expected_count = len(MODULE_STEP_KEYS["module_4"])
    expected = [f"module_{n}" for n in (1, 1, 1, 1, 2, 2, 2, 3, 4, 4)]
    report = {"scope": "local graph + deterministic provider; no network/production DB",
              "session_id": session_id, "round_count": len(rows), "rounds": rows,
              "module_sequence_ok": [row["actual_module"] for row in rows] == expected,
              "goal_evidence_created": goal is not None,
              "goal_evidence": goal,
              "m4_completed_steps": m4_steps,
              "m4_contract_ok": len(m4_steps) == m4_expected_count,
              "validator_blocked_replies": [row["round"] for row in rows if row["validator_status"] == "blocked"],
              "status": "passed" if (
                  len(rows) == 10 and [row["actual_module"] for row in rows] == expected
                  and goal is not None and len(m4_steps) == m4_expected_count
                  and not [row for row in rows if row["validator_status"] == "blocked"]
              ) else "findings"}
    out_dir = ROOT / "work" / "local-dialogue-0919"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dialogue.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    transcript = ["# 本地十轮对话验收记录", "", "> 使用真实 Graph turn 调用和真实契约/validator；Provider 为本地确定性脚本，未调用外部模型。", ""]
    for row in rows:
        transcript.extend([f"## 第 {row['round']} 轮 · {row['expected_module']}",
                           f"- 用户：{row['input']}", f"- Agent：{row['reply']}",
                           f"- 实际模块：{row['actual_module']}；下一模块：{row['next_module']}；路由待处理：{row['routing_pending']}",
                           f"- Validator：{row['validator_status']}；耗时：{row['elapsed_ms']} ms", ""])
    transcript.extend(["## 验收结果", "", f"- 总体状态：**{report['status']}**",
                       f"- 目标证据：{'通过' if goal else '失败'}",
                       f"- M4 契约：{'通过' if report['m4_contract_ok'] else '失败'}",
                       "- 说明：该运行验证本地流程、门控与状态传递，不代表真实 LLM 的主观回复质量或线上性能。"])
    (out_dir / "DIALOGUE_TRANSCRIPT.md").write_text("\n".join(transcript) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "round_count": len(rows),
                      "goal_evidence_created": report["goal_evidence_created"],
                      "m4_contract_ok": report["m4_contract_ok"],
                      "report": str(out_dir / "DIALOGUE_TRANSCRIPT.md")}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
