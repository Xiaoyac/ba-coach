"""Local-only M1 -> M4 adversarial stress acceptance.

This harness deliberately uses an in-memory session store, a deterministic
provider and the project's real graph/contracts/validator. It never loads
`.env`, production credentials, a production database or the public site.

Each cycle runs four graph turns plus contract and behavior-rule checks. The
default is 1,000 cycles; `--workers` adds concurrent pressure without sharing
sessions. Every failure is retained with a compact input/output fingerprint,
not private transcript text.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.answer_validator import validate_answer
from app.config import Settings
from app.graph import get_graph
from app.graph import nodes as nodes_module
from app.graph.state import GraphContext
from app.goal_contract import proposal_evidence
from app.m1_contract import normalize as normalize_m1
from app.m4_contract import normalize as normalize_m4
from app.retrieval import StubKnowledgeBase
from app.schemas import Message
from app.session import InMemorySessionStore
from app.workflow_contract import MODULE_STEP_KEYS


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:12]


class StressProvider:
    """Canned provider: exercises graph plumbing without network/model cost."""
    name = "stress-synthetic"
    model = "stress-synthetic-v1"

    def __init__(self):
        self.complete_calls = 0
        self.route_calls = 0
        self.stream_calls = 0
        self.detailed_calls = 0
        self.route_result = '{"target_module":"1"}'

    async def complete(self, *, system, messages):
        from app.providers.base import Completion
        self.complete_calls += 1
        module = next((f"module_{n}" for n in range(1, 5) if f"reply_module: module_{n}" in str(system)), "module_1")
        # No unsupported completion claims, panel instructions, clinical claims,
        # or invented KB identifiers. The answer validator sees this every turn.
        text = {
            "module_1": "我们先把这次经历说清楚，再一起看看下一步。",
            "module_2": "我们可以继续讨论一个你愿意尝试的具体行动。",
            "module_3": "我们把记录方式谈到你觉得可行，再按你的节奏执行。",
            "module_4": "我们先回看这次真实经历，再由你决定下一步。",
        }[module]
        return Completion(text=text, model=self.model, usage={"input_tokens": 8, "output_tokens": 12},
                          reasoning_content="合成验收思考", finish_reason="stop", request_id="stress")

    async def stream(self, *, system, messages):
        from app.providers.base import StreamDelta
        self.stream_calls += 1
        for part in ("我们先", "一起看看", "下一步。"):
            yield StreamDelta(kind="content", text=part)
        yield StreamDelta(kind="usage", usage={"input_tokens": 8, "output_tokens": 12}, finish_reason="stop", request_id="stress")

    async def classify(self, *, system, user, allowed, default):
        return default

    async def route(self, *, system, user, max_tokens=None):
        self.route_calls += 1
        return self.route_result

    async def route_with_reasoning(self, *, system, user, max_tokens=None):
        from app.providers.base import Completion
        self.route_calls += 1
        return Completion(text=self.route_result, model=self.model, reasoning_content="路由合成思考", finish_reason="stop")

    async def route_detailed(self, *, system, user, max_tokens=None):
        from app.providers.base import Completion
        self.detailed_calls += 1
        # Risk/extraction callers parse a strict JSON object. An empty object
        # is a conservative no-signal result for this non-risk synthetic turn.
        return Completion(text="{}", model=self.model, finish_reason="stop", request_id="stress-detailed")


def m1_fixture(i):
    trigger = f"第{i}轮下班看到学习资料"
    feeling = "焦虑"
    behavior = "躺下刷手机"
    consequence = "后来更焦虑"
    turns = [
        ("user", f"{trigger}，我很{feeling}，于是{behavior}，{consequence}。"),
        ("assistant", f"你{trigger}后{feeling}，{behavior}，之后{consequence}。"),
        ("user", "这个总结基本符合。"), ("user", "我试过先列小清单。"),
        ("assistant", "行动和情绪、精力会相互影响。"),
        ("assistant", "活动和反馈减少可能维持困扰。"),
        ("assistant", "从可调整的行动入手可能获得新反馈。"),
        ("assistant", "行动不保证立刻开心。"),
        ("assistant", "可以尝试、观察反馈、再调整。"),
        ("user", "我理解了，也愿意开始目标设定。"),
    ]
    raw = {"path": "personalized", "fact_quotes": {"trigger": trigger, "feeling": feeling, "behavior": behavior, "consequence": consequence},
           "summary_quote": turns[1][1], "approval_quote": turns[2][1], "methods_quote": turns[3][1],
           "education_quotes": [x[1] for x in turns[4:9]], "understanding_quote": turns[9][1],
           "consent_quote": turns[9][1], "core_questions_resolved": True}
    data = {"chief_complaint": "最近工作后很难开始学习", "trigger_situation": trigger,
            "coping_behavior": behavior, "coping_consequence": consequence,
            "abc_event": {"trigger": trigger, "feeling": feeling, "behavior": behavior, "consequence": consequence},
            "ai_depression_cycle_summary": turns[1][1], "user_approval_level": 2,
            "attempted_relief_methods": ["先列小清单"]}
    return raw, data, turns


def m2_fixture(i, secondary=False):
    direction = "找回生活节奏" if not secondary else None
    activity = f"站桩十分钟-{i}" if not secondary else f"周末游泳-{i}"
    body = f"我想试试{activity}。" if secondary else f"我想{direction}，选择{activity}。"
    message = SimpleNamespace(id=i * 10 + 1, position=1, role="user", content=body, conversation_id=f"stress-{i}")
    raw = {"goal_kind": "secondary" if secondary else "primary", "long_term_direction": direction,
           "selection_quote": body, "activity_quote": activity,
           "direction_quote": (body if not secondary else None)}
    return raw, [message], activity


def m4_fixture(i):
    # M4 normalization consumes the persisted DB message shape, including
    # position; frontend Message intentionally has no such field.
    def msg(n, role, content):
        return SimpleNamespace(id=i * 100 + n, position=n, role=role, content=content)
    msgs = [
        msg(1, "user", "昨天晚上在家"), msg(2, "user", "我做了站桩十分钟"),
        msg(3, "user", "做完后觉得轻松"),
        msg(4, "assistant", "我们总结：昨天晚上在家，你站桩十分钟，做完后觉得轻松。"),
        msg(5, "user", "这个总结准确"), msg(6, "assistant", "行为和情绪会相互影响。"),
        msg(7, "user", "我理解了"), msg(8, "user", "这次没有困难"),
        msg(9, "user", "我决定继续原计划"),
        msg(10, "assistant", "这次复盘完成，下一步按你的决定继续。"),
    ]
    data = {"phase_a": {"event": "昨天晚上在家"},
            "phase_b": {"overt": {"activity": "站桩", "action_taken": True, "completion_status": "complete", "actual_duration_minutes": 10}},
            "phase_c": {"effect": "做完后觉得轻松"}, "ai_abc_chain_summary": msgs[3].content,
            "ba_reeducation_content": msgs[5].content, "core_difficulty_type": None,
            "difficulty_description": None, "next_coping_strategy": None,
            "review_decision": 1, "review_summary": msgs[9].content,
            "m4_contract": {"phase_a_quote": msgs[0].content, "phase_b_quote": msgs[1].content,
                "phase_c_quote": msgs[2].content, "emotion_improved": True, "emotion_quote": msgs[2].content,
                "pre_action_barrier": None, "barrier_quote": None, "window_closed": False, "window_quote": None,
                "summary_quote": msgs[3].content, "chain_status": "confirmed", "confirmation_quote": msgs[4].content,
                "education_quote": msgs[5].content, "understanding_quote": msgs[6].content,
                "core_questions_resolved": True, "difficulty_status": "none", "difficulty_quote": msgs[7].content,
                "strategy_quote": None, "decision_quote": msgs[8].content, "review_summary_quote": msgs[9].content}}
    return data, msgs


def behavior_checks():
    bad = [
        ("module_1", "从明天开始你应该每天跑步"),
        ("module_2", "我们已经确定目标已确定"),
        ("module_3", "改成跑步目标"),
        ("module_4", "你本周坚持了五天"),
    ]
    findings = []
    for module, reply in bad:
        result = validate_answer(reply=reply, module=module, evidence_ids=[], workflow={"available": True, "current_module": module, "plan_confirmed": False})
        if result["status"] == "passed":
            findings.append({"kind": "validator_failed_to_block", "module": module, "fingerprint": digest(reply)})
    panel = validate_answer(reply="请去目标面板确认并保存目标。", module="module_2", evidence_ids=[], workflow={"available": True, "current_module": "module_2", "plan_confirmed": False})
    if panel["status"] != "blocked":
        findings.append({"kind": "panel_instruction_not_blocked"})
    return findings


async def run_cycle(i: int, *, provider: StressProvider, failures: list, latencies: list, sem: asyncio.Semaphore):
    async with sem:
        started = time.perf_counter()
        try:
            raw, data, turns = m1_fixture(i)
            m1 = normalize_m1(raw, data, turns, f"stress-{i}")
            if m1["completed_steps"] != list(MODULE_STEP_KEYS["module_1"]):
                raise AssertionError(f"M1 incomplete: {m1['missing_fields']}")
            for secondary in (False, True):
                goal_raw, goal_msgs, activity = m2_fixture(i, secondary)
                evidence = proposal_evidence(goal_raw, goal_msgs, activity)
                if evidence is None or evidence["goal_kind"] != goal_raw["goal_kind"]:
                    raise AssertionError(f"M2 goal evidence rejected ({goal_raw['goal_kind']})")
            m3 = list(MODULE_STEP_KEYS["module_3"])
            if len(m3) != 3 or len(set(m3)) != 3:
                raise AssertionError("M3 registry invalid")
            m4_data, m4_messages = m4_fixture(i)
            m4 = normalize_m4(m4_data, m4_messages, session_id=f"stress-{i}", cycle_id=f"cycle-{i}", assistant_message_id=i * 100 + 10)
            m4_contract = m4["phase_c"]["_m4_contract"]
            if m4_contract["completed_steps"] != list(MODULE_STEP_KEYS["module_4"]):
                raise AssertionError(f"M4 incomplete: {m4_contract['missing_fields']}")
            store = InMemorySessionStore(ttl_seconds=3600, max_messages=40)
            settings = Settings(_env_file=None, knowledge_intent_gate_enabled=False,
                                knowledge_mediator_enabled=False, answer_validator_enabled=True,
                                database_schema_version="legacy")
            context = GraphContext(provider=provider, router_provider=provider, store=store,
                                   knowledge_base=StubKnowledgeBase(), settings=settings, stream=False)
            session_id = (await store.get_or_create(None)).session_id
            for module, prompt in (("module_1", "我想聊聊最近的困难"), ("module_2", "我想讨论一个行动"),
                                   ("module_3", "我想谈谈怎么记录"), ("module_4", "我想回顾这次经历")):
                result = await get_graph().ainvoke({"session_id": session_id, "user_input": prompt,
                    "forced_module": module, "metadata": {}}, context=context)
                if result.get("extracted_intent") != module:
                    raise AssertionError(f"route mismatch {module}->{result.get('extracted_intent')}")
                if not str(result.get("final_response") or "").strip():
                    raise AssertionError(f"empty agent response in {module}")
                validation = validate_answer(reply=result["final_response"], module=module, evidence_ids=[], workflow={
                    "available": True, "current_module": module, "plan_confirmed": False})
                if validation["status"] != "passed":
                    raise AssertionError(f"agent response validator status={validation['status']} module={module}")
            latencies.append((time.perf_counter() - started) * 1000)
        except Exception as exc:
            failures.append({"cycle": i, "kind": type(exc).__name__, "fingerprint": digest(str(exc)), "detail": str(exc)[:240]})


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--output", default=str(ROOT / ".test-tmp" / "m1-m4-stress-1000-report.json"))
    args = parser.parse_args()
    if args.cycles < 1000:
        raise SystemExit("refusing fewer than 1000 cycles")
    failures, latencies = [], []
    behavior_failures = behavior_checks()
    provider = StressProvider()
    sem = asyncio.Semaphore(max(1, args.workers))
    started = time.perf_counter()
    # Batches keep event-loop pressure high while allowing progress snapshots.
    for offset in range(0, args.cycles, 100):
        batch = [asyncio.create_task(run_cycle(i, provider=provider, failures=failures,
                    latencies=latencies, sem=sem)) for i in range(offset, min(offset + 100, args.cycles))]
        await asyncio.gather(*batch)
        if offset % 200 == 0:
            print(json.dumps({"completed": min(offset + 100, args.cycles), "failures": len(failures),
                              "elapsed_s": round(time.perf_counter() - started, 2)}), flush=True)
    elapsed = time.perf_counter() - started
    latencies.sort()
    p95 = latencies[min(len(latencies) - 1, math.ceil(len(latencies) * .95) - 1)] if latencies else None
    report = {"scope": "local synthetic only", "cycles_requested": args.cycles, "cycles_completed": args.cycles,
              "full_graph_turns": args.cycles * 4, "concurrency_workers": args.workers,
              "elapsed_seconds": round(elapsed, 3), "throughput_cycles_per_second": round(args.cycles / elapsed, 3),
              "latency_ms": {"min": round(min(latencies), 3) if latencies else None,
                            "median": round(statistics.median(latencies), 3) if latencies else None,
                            "p95": round(p95, 3) if p95 is not None else None,
                            "max": round(max(latencies), 3) if latencies else None},
              "provider_calls": {"complete": provider.complete_calls, "route": provider.route_calls,
                                 "route_detailed": provider.detailed_calls, "stream": provider.stream_calls},
              "behavior_rule_failures": behavior_failures, "failures": failures,
              "status": "passed" if not failures and not behavior_failures else "failed",
              "notes": ["No .env loaded; no production DB/API/website; no user data.",
                        "M1/M2/M3/M4 contracts and real graph turn plumbing were exercised.",
                        "Agent response quality is checked against deterministic safety/workflow rules, not subjective counseling quality."]}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "cycles": args.cycles, "failures": len(failures),
                      "behavior_rule_failures": len(behavior_failures), "elapsed_s": round(elapsed, 3),
                      "report": args.output}, ensure_ascii=False), flush=True)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
