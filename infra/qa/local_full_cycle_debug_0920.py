"""Local black-box M1 -> M4 cycle debugger.

This runner talks to a *local* HTTP server only.  It creates one fresh account
per scenario, creates a normal conversation, and sends ordinary ``/api/chat``
turns.  It never pins a module, calls a confirmation endpoint, or mutates
program state directly.  Program/conversation endpoints are used read-only to
settle and record the state after each turn.

The scripted personas are deliberately varied so a batch covers successful,
partial and unstarted execution, all four M4 decisions, natural confirmations,
and a plan edit followed by re-confirmation.  The script is a debugger rather
than a usability study: the transcript records what the model actually did.

Usage (from the repository root)::

    python infra/qa/local_full_cycle_debug_0920.py pilot --scenario 1
    python infra/qa/local_full_cycle_debug_0920.py batch --count 10

The default base URL is ``http://127.0.0.1:8012``.  Any non-loopback URL is
rejected to prevent accidentally sending test conversations to production.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import socket
import string
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".test-tmp" / "local-cycle-debug"
DEFAULT_BASE = os.environ.get("LOCAL_CYCLE_BASE_URL", "http://127.0.0.1:8012")
DEFAULT_PROVIDER = os.environ.get("LOCAL_CYCLE_PROVIDER", "deepseek")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".pending")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def require_local_base(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(f"Refusing non-local base URL {base_url!r}; this runner only accepts loopback hosts")
    return base_url.rstrip("/")


@dataclass(frozen=True)
class Scenario:
    number: int
    name: str
    activity: str
    place: str
    duration: int
    barrier: str
    coping: str
    execution: str
    decision: str
    replan: bool = False
    natural_confirmation: bool = True
    expected_next_duration: int | None = None


SCENARIOS = [
    Scenario(1, "complete_end", "晚饭后散步", "小区楼下", 10, "下雨", "改在楼道走五分钟", "complete", "end"),
    Scenario(2, "complete_continue", "客厅伸展", "客厅", 5, "工作忙", "睡前伸展两分钟", "complete", "continue"),
    Scenario(3, "partial_adjust_replan", "走廊步行", "办公室走廊", 10, "临时加班", "先走三分钟", "partial", "adjust", True, expected_next_duration=3),
    Scenario(4, "partial_end", "整理房间", "卧室", 10, "太累", "先整理两分钟", "partial", "end"),
    Scenario(5, "not_started_pause", "跟音乐活动", "客厅", 5, "没兴致", "先听一首歌", "not_started", "pause"),
    Scenario(6, "not_started_continue", "阳台站立", "阳台", 5, "工作太忙", "先站两分钟", "not_started", "continue"),
    Scenario(7, "no_improvement_end", "楼下慢走", "楼下花园", 10, "下雨", "走楼道五分钟", "no_improvement", "end"),
    Scenario(8, "alternative_adjust_replan", "整理书架", "书房", 10, "疲惫", "先整理两分钟", "alternative", "adjust", True, expected_next_duration=3),
    Scenario(9, "complete_pause", "浇花", "阳台", 5, "忘记", "晚饭后看花盆", "complete", "pause"),
    Scenario(10, "partial_continue", "原地踏步", "客厅", 5, "工作忙", "先踏步一分钟", "partial", "continue"),
]


M1_MESSAGES = [
    "最近下班后我总是没精神，想活动又拖着不动，有些烦躁和自责。我想聊聊这个问题。",
    "昨天晚饭后我看到门口的运动鞋，本来想出门走走。我想今天太累了明天再说，身体沉重，心情低落，于是躺在沙发刷手机两个小时。当时松口气，后来更自责，更不想动了。",
    "你总结的情境、想法、情绪身体感受、行为和后果符合我的经历。我以前也试过强迫自己跑步，坚持两天就放弃，后来发现先走五分钟更容易。",
    "我理解低落时回避活动会暂时轻松，却减少积极体验，让情绪更差；先做一个小而可行的活动，再观察感受，可以慢慢打破循环，不是等心情好才动，也不保证马上开心。",
    "这个解释和我的经历贴合，我没有疑问了，也愿意尝试，请继续和我讨论一个自己愿意做的小活动目标。",
    "是的，你对我的理解准确；我已经理解这个原理，也确认愿意一起制定下一步的小行动，不需要继续重复确认。",
    "我理解了，也愿意进入目标设定。",
    "可以，我们开始讨论具体的活动安排吧。",
]


def execution_text(s: Scenario) -> str:
    # Re-plan scenarios execute against the M2 revised five-minute card.  M4
    # may then choose a distinct expected_next_duration (three minutes in the
    # adjust personas); that later value belongs in the successor draft, not
    # in this already completed execution report.
    planned_duration = 5 if s.replan else s.duration
    plan_label = "修改后的计划" if s.replan else "计划"
    original_label = "修改后的原计划" if s.replan else "原计划"
    return {
        "complete": f"这次我按{plan_label}完成了{s.activity}{planned_duration}分钟。开始前有点懒但没有实际障碍，想着先做一点就开始了。完成后心情从2分变3分，身体舒展。",
        "partial": f"我实际做了{s.activity}三分钟后停下，{plan_label}是{planned_duration}分钟，部分完成。因为{s.barrier}有点犹豫，想着先做一点也好。完成后心情从2分变3分，身体轻松一点。",
        "not_started": f"这次我没有开始{s.activity}，实际零分钟。因为{s.barrier}我想着以后再做，躺下刷手机。当时松口气，后来失落，心情还是2分。",
        "no_improvement": f"我完成了{s.activity}{planned_duration}分钟，开始前没有障碍。做完身体舒展了，但心情还是2分没有改善。这是不是没用？",
        "alternative": f"{original_label}是{s.activity}{planned_duration}分钟，但这次没有做，实际零分钟。因为{s.barrier}觉得麻烦，但另外临时散步五分钟，做完心情从2分到3分。散步不是原计划，不要算原目标完成。",
    }[s.execution]


def decision_text(decision: str, variant: int = 0, *, minutes: int = 5) -> str:
    """Render varied but semantically explicit M4 decisions."""
    options = {
        "end": [
            "这一轮我就到这里，结束这个目标，不开启下一周期。",
            "我想把这个目标结束掉，保留本轮历史，后面不再自动继续。",
            "这次复盘后我决定收尾，不再开下一轮。",
        ],
        "pause": [
            "我先把这个目标暂停，历史保留，过后再决定是否恢复。",
            "目前我想暂时停一下这个目标，但不删除已有记录。",
            "我的选择是暂停这项目标，之后需要时再恢复。",
        ],
        "continue": [
            "我想沿用同一个目标和同一份计划，进入下一次执行。",
            "这次继续原来的安排，开启新的执行周期，不另建目标。",
            "我决定保持计划不变，再做一个周期看看。",
        ],
        "adjust": [
            f"我想保留同一个目标，但把每次时长改成{minutes}分钟，重新讨论这份计划。",
            f"目标方向不变，我要调整计划为每次{minutes}分钟，再确认新版本。",
            f"我选择调整原目标的时长到{minutes}分钟，进入新的计划版本。",
        ],
    }
    return options[decision][variant % len(options[decision])]


def messages_for(s: Scenario) -> dict[str, list[str]]:
    start = "明天晚上八点"
    first_plan = (
        f"我想找回下班后的生活节奏和精力，这个方向对我很重要，因为状态稳定后我更能照顾工作和生活。"
        f"我理解行为激活不是等心情变好，而是先做一个小而可行的活动，再观察真实感受。"
        f"我知道身体活动也包括日常具体活动，不只体育锻炼。"
        f"基于这个方向，我选择{s.activity}作为主要目标，开始时间是{start}，在{s.place}做{s.duration}分钟，"
        f"每天一次，先试三天。难度2分，我觉得做得到，没有身体限制，也不需要同伴。"
        f"可能遇到{s.barrier}，应对是{s.coping}。请和我整理这个计划。"
    )
    plan_followup = "这个目标和我想找回生活节奏的方向一致，我自己愿意做，也有场地和时间。请把活动、时间、地点、时长、频率、障碍和应对整理成计划让我确认。"
    plan_quality = f"我觉得这个计划难度是2分，活动和安排合理，先试三天能检验是否适合我。"
    card_confirmations = [
        "我看过刚才完整的计划了，安排符合我的实际情况，就按这个计划试试。",
        "活动、时间和应对办法都对得上我的生活，我愿意照这张计划卡开始。",
        "这份安排是我自己选的，也确实做得到，我确认按它执行。",
        "我读完了计划摘要，没有需要修改的地方，可以按这个安排开始。",
    ]
    natural_card_confirm = card_confirmations[s.number % len(card_confirmations)]
    replan = f"我想把时长从{s.duration}分钟改为五分钟，其他安排不变。请按修改后的计划重新整理，我会再确认。"
    replan_confirm = "我确认修改后的计划：每天晚上在原地点做五分钟，遇到原来的障碍按刚才的应对办法处理，就这样执行。"
    m2 = [first_plan, plan_followup, plan_quality]
    if s.replan:
        # Do not confirm the first card before editing it.  The edit is sent
        # while M2 is still awaiting confirmation; only the revised card gets
        # a confirmation, so a false positive cannot come from an old hash.
        m2 += [replan, "修改后的安排清楚，也符合我能做到的程度。", replan_confirm,
               "我看过刚才重新整理的完整计划，愿意按这份五分钟的安排执行。"]
    else:
        m2 += [natural_card_confirm, "这份计划我已经看过，确认没有问题，同意按它开始尝试。"]
    m3_confirmations = [
        "这个记录方式我能做到，就这么安排吧。",
        "我确认并同意这个记录方法。",
        "时间、活动内容和做完后的心情评分都符合我的实际情况，我愿意照此执行。",
        "我已经看过记录约定，没有需要修改，确认按这份记录。",
        "每天晚上这样记很方便，遇到困难回来反馈，我同意这份约定。",
    ]
    m3 = [
        "我愿意每天晚上做完后打开左侧记录今日，记录活动时间、活动内容和做完后的心情，心情用0到5分，其他感受选填，遇到困难回来聊天。请整理记录办法让我确认。",
        m3_confirmations[s.number % len(m3_confirmations)],
        m3_confirmations[(s.number + 1) % len(m3_confirmations)],
        m3_confirmations[(s.number + 2) % len(m3_confirmations)],
        m3_confirmations[(s.number + 3) % len(m3_confirmations)],
    ]
    chosen_decision = decision_text(s.decision, s.number)
    m4 = [
        "现在已经过了约定的执行时间，我来回顾刚才的活动。" + execution_text(s),
        "你总结的事件、想法、情绪和行为后果符合我的实际体验，我确认这个总结。",
        f"我理解了，身体活动和心情会相互影响，先做可行的小行动再观察，做完不保证立刻开心，也不需要硬撑。我没有疑问。如果再遇到{s.barrier}，我愿意采用{s.coping}这个办法。" + chosen_decision,
        "我确认这次复盘，也确认我的下一步决定。" + chosen_decision,
        "对，这正是我自己的决定。请按这个决定完成本轮。" + chosen_decision,
    ]
    return {"module_1": M1_MESSAGES, "module_2": m2, "module_3": m3, "module_4": m4}


def _compact_text(value: Any) -> str:
    """Normalize only whitespace when checking what the assistant displayed."""
    return re.sub(r"\s+", "", str(value or ""))


def _assistant_displayed(value: Any, conversation: dict[str, Any]) -> bool:
    """Return true only when the exact draft text occurs in an assistant turn.

    The check deliberately ignores whitespace (models often turn paragraph
    breaks into spaces) but never accepts a paraphrase or a user message.  It
    is therefore safe for the M4 persona to acknowledge only evidence it has
    actually seen in the chat.
    """
    needle = _compact_text(value)
    if not needle:
        return False
    for message in conversation.get("messages") or []:
        if message.get("role") != "assistant":
            continue
        if needle in _compact_text(message.get("content")):
            return True
    return False


def _latest_assistant_context(conversation: dict[str, Any]) -> tuple[int, str]:
    """Return the latest assistant index/content for turn-order decisions."""
    messages = conversation.get("messages") or []
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "assistant":
            return index, str(messages[index].get("content") or "")
    return -1, ""


def _user_understanding_after(messages: list[dict[str, Any]], assistant_index: int) -> bool:
    """Detect an understanding quote after a specific assistant explanation."""
    if assistant_index < 0:
        return False
    for message in messages[assistant_index + 1:]:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if re.search(r"理解|明白|没有疑问|没问题|对得上|愿意采用|按这个办法", content):
            return True
    return False


def _draft(program: dict[str, Any]) -> dict[str, Any]:
    value = program.get("draft")
    return value if isinstance(value, dict) else {}


def _missing(program: dict[str, Any]) -> set[str]:
    direct = program.get("missing_fields")
    if isinstance(direct, list):
        return {str(item) for item in direct}
    readiness = program.get("readiness") or {}
    values = readiness.get("missing_fields") if isinstance(readiness, dict) else None
    return {str(item) for item in values or []}


def _fact_line(scenario: Scenario, draft: dict[str, Any]) -> str:
    """Render facts already extracted from the current M4 draft.

    Fallbacks mirror the scripted user message so the runner can ask for a
    summary without pretending that an assistant summary was already shown.
    """
    phase_a = draft.get("phase_a") or {}
    phase_b = draft.get("phase_b") or {}
    phase_c = draft.get("phase_c") or {}
    overt = phase_b.get("overt") or {}
    short_term = phase_c.get("short_term") or {}
    trigger = phase_a.get("trigger") or scenario.barrier
    activity = overt.get("activity") or scenario.activity
    actual_duration = overt.get("actual_duration_minutes")
    if actual_duration is None:
        actual_duration = {"complete": 5 if scenario.replan else scenario.duration,
                           "partial": 3, "not_started": 0,
                           "no_improvement": 5 if scenario.replan else scenario.duration,
                           "alternative": 0}.get(scenario.execution, 0)
    # Do not invent a coping/action detail when the extractor omitted it.
    # The initial execution message remains the source of truth; the next
    # turn can ask the model to quote it or request the missing fact instead.
    coping = phase_b.get("coping_method")
    emotion_change = short_term.get("emotion_change")
    action = f"我采取了{coping}；" if coping else "本轮应对方式以实际原话为准；"
    effect = emotion_change or "感受以本轮用户原话为准"
    return (f"情境/阻碍是{trigger}；{activity}实际{actual_duration}分钟；"
            f"{action}{effect}。")


def adaptive_m4_message(scenario: Scenario, program: dict[str, Any],
                        conversation: dict[str, Any], *, round_no: int,
                        initial_message: str) -> tuple[str, str]:
    """Choose one bounded, evidence-aware M4 user turn.

    The first M4 turn reports execution.  Later turns respond to the current
    server-side missing fields and only acknowledge ABC/BA text that appears
    in an assistant message in this conversation.  This keeps the harness a
    natural black-box client while avoiding fixed sentence jumps.
    """
    if round_no == 1:
        return initial_message, "m4_initial_execution_feedback"

    draft = _draft(program)
    missing = _missing(program)
    summary = draft.get("abc_chain_summary")
    education = draft.get("ba_reeducation_content")
    summary_shown = _assistant_displayed(summary, conversation)
    education_shown = _assistant_displayed(education, conversation)
    messages = conversation.get("messages") or []
    latest_assistant_index, latest_assistant = _latest_assistant_context(conversation)
    # The extractor can lag behind the visible reply.  Treat a fresh assistant
    # reply containing the BA explanation as a turn-order signal even when
    # ba_reeducation_content is not persisted yet.  Understanding must follow
    # this reply; otherwise a stale m2 missing flag makes the harness repeat
    # ABC confirmation and skip the education gate.
    latest_ba_explanation = bool(
        (education and _compact_text(education) in _compact_text(latest_assistant))
        or re.search(r"行为和心情|行动反过来|行动可以先于|不一定每次|不保证.*开心|动机不一定|按BA", latest_assistant)
    )
    if latest_ba_explanation:
        education_shown = True
    understanding_after_latest = _user_understanding_after(messages, latest_assistant_index)
    latest_summary_question = bool(
        ((summary and _compact_text(summary) in _compact_text(latest_assistant))
         or re.search(r"(事件|情境).{0,24}(想法|念头).{0,24}(情绪|感受).{0,24}(行为|后果)", latest_assistant))
        and re.search(r"对得上|符合|核对|确认|总结", latest_assistant)
    )
    facts = _fact_line(scenario, draft)
    decision = decision_text(
        scenario.decision, scenario.number,
        minutes=scenario.expected_next_duration or 5,
    )

    if "m4_milestone_3" in missing and latest_ba_explanation and not understanding_after_latest:
        return (
            f"我理解了：行为和心情会相互影响，{facts}"
            f"如果再遇到{scenario.barrier}，我愿意采用{scenario.coping}这个办法。"
            f"{decision}",
            "m4_followup_understanding_after_latest_education",
        )

    if {"m4_milestone_2", "chain_confirmation_status"} & missing:
        if summary and summary_shown and latest_summary_question:
            return (
                f"我看过你刚才展示的完整ABC总结，其中{facts}这和我的真实经历一致，"
                "我确认这份总结。",
                "m4_followup_acknowledge_displayed_abc",
            )
        return (
            f"我刚才实际说的是：{facts}我还没有看到一段完整的ABC整理，"
            "请先把这组事实整理成连续的情境、想法或阻碍、行动和即时及后续感受，"
            "发给我核对；我核对后再进入下一步。",
            "m4_followup_request_abc_summary_before_confirmation",
        )

    if "m4_milestone_3" in missing:
        if education and education_shown:
            return (
                f"我理解了：行为和心情会相互影响，{facts}"
                f"如果再遇到{scenario.barrier}，我愿意采用{scenario.coping}这个办法。"
                f"{decision}",
                "m4_followup_confirm_ba_understanding_strategy_and_decision",
            )
        confirmed_basis = "刚才展示并核对的ABC总结" if summary_shown else "刚才核对的实际事实"
        return (
            f"我确认{confirmed_basis}。请解释这次行动（或没有开始）怎样带来当时的"
            "放松和后来的感受，以及BA如何理解这种关系；我听完后会说明自己的理解。",
            "m4_followup_request_ba_reeducation_before_understanding",
        )

    if "m4_milestone_4" in missing:
        functional = ((draft.get("phase_c") or {}).get("functional_analysis")
                      or "先前的应对让当下轻松，却没有改变之后的感受")
        return (
            f"我理解这次行动和感受的关系：{functional}。"
            f"如果再遇到{scenario.barrier}，我愿意采用{scenario.coping}这个办法。{decision}",
            "m4_followup_state_understanding_and_keep_decision",
        )

    if "m4_milestone_5" in missing:
        summary_basis = "这份已展示并核对的ABC总结" if summary_shown else "已经核对的实际事实"
        return (
            f"请围绕已经核对的实际事实（{facts}）、{summary_basis}、我已经作出的"
            f"“{decision}”决定，整理一段完整的本轮复盘摘要；这个决定保持不变。",
            "m4_followup_request_complete_review_without_changing_decision",
        )

    return (
        f"我确认以上已经核对的复盘事实和“{decision}”，请按这份内容完成本轮记录，"
        "不要替我改变决定。",
        "m4_followup_wait_for_persisted_readiness",
    )


@dataclass
class Client:
    base: str
    scenario: Scenario
    timeout: tuple[int, int] = (15, 180)
    session: requests.Session = field(default_factory=requests.Session)
    username: str = ""
    session_id: str | None = None

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        response = self.session.request(method, self.base + path, timeout=self.timeout, **kwargs)
        return response

    def json_api(self, method: str, path: str, *, expected: set[int] | None = None, **kwargs: Any) -> Any:
        response = self.request(method, path, **kwargs)
        if expected is None:
            expected = set(range(200, 300))
        if response.status_code not in expected:
            detail = response.text[:800].replace("\n", " ")
            raise RuntimeError(f"{method} {path} HTTP {response.status_code}: {detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"{method} {path} returned non-JSON") from exc

    def register(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.username = "lc0920" + suffix
        payload = {
            "username": self.username,
            "password": "LocalCycle!" + uuid.uuid4().hex,
            # Pydantic's EmailStr rejects reserved special-use TLDs such as
            # ``.invalid``.  The local deployment has email delivery disabled;
            # example.com is therefore only a syntactically valid fixture.
            "email": self.username + "@example.com",
            "nickname": "本地周期" + self.scenario.name[:8],
            "tag": str(random.randint(10000, 99999)),
            "birth_date": "1990-01-01",
            "living_status": "和家人",
            "communication_preference": "温柔引导",
            "physical_condition": [],
            "behavior_taboo": [],
        }
        data = self.json_api("POST", "/api/auth/register", expected={200, 201}, json=payload)
        token = data.get("token")
        if not token:
            raise RuntimeError("registration returned no token")
        self.session.headers.update({"Authorization": "Bearer " + token})

    def new_conversation(self) -> dict[str, Any]:
        data = self.json_api("POST", "/api/conversations", expected={200, 201})
        self.session_id = data["session_id"]
        return data

    def program(self) -> dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("conversation has not been created")
        return self.json_api("GET", "/api/program/" + self.session_id)

    def conversation(self) -> dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("conversation has not been created")
        return self.json_api("GET", "/api/conversations/" + self.session_id)

    def overview(self) -> dict[str, Any]:
        return self.json_api("GET", "/api/program/goals/overview")

    def settle(self, before_module: str | None, seconds: float,
               previous_assistant_id: int | None = None) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        """Poll read endpoints until this turn's Router work is durable.

        A module pointer can legitimately remain unchanged (for example an
        M4 ``continue`` decision starts another M4 cycle).  In that case a
        fixed sleep is both slow and racy.  The latest *new* assistant message
        carries the Router completion marker (`router_model_name` or
        `timing.router_processing_ms`) once the detached routing task has
        finished.  We then read program/conversation once more to close the
        marker-to-state race before returning.
        """
        deadline = time.monotonic() + seconds
        observations: list[dict[str, Any]] = []
        latest_program: dict[str, Any] = {}
        latest_conversation: dict[str, Any] = {}
        while True:
            latest_program = self.program()
            latest_conversation = self.conversation()
            runtime = latest_program.get("runtime") or {}
            assistant_messages = [m for m in (latest_conversation.get("messages") or []) if m.get("role") == "assistant"]
            latest_assistant = assistant_messages[-1] if assistant_messages else {}
            latest_assistant_id = latest_assistant.get("id")
            timing = latest_assistant.get("timing") or {}
            route_marker = bool(latest_assistant.get("router_model_name") or timing.get("router_processing_ms") is not None)
            new_assistant = previous_assistant_id is None or latest_assistant_id != previous_assistant_id
            observations.append({
                "at": now_iso(),
                "module": runtime.get("current_module"),
                "flow_status": runtime.get("flow_status"),
                "row_version": runtime.get("row_version"),
                "last_transition_reason": runtime.get("last_transition_reason"),
                "active_goal_id": runtime.get("active_goal_id"),
                "active_cycle_id": runtime.get("active_cycle_id"),
                "draft": bool(latest_program.get("draft")),
                "missing_fields": latest_program.get("missing_fields") or [],
                "conversation_next_module": latest_conversation.get("next_module"),
                "latest_assistant_id": latest_assistant_id,
                "route_marker": route_marker,
                "new_assistant": new_assistant,
            })
            current = runtime.get("current_module")
            if new_assistant and route_marker:
                # The marker update and runtime transaction are separate
                # writes.  Re-read both endpoints after seeing the marker so
                # the caller cannot observe the old module in between.
                latest_program = self.program()
                latest_conversation = self.conversation()
                final_runtime = latest_program.get("runtime") or {}
                observations.append({
                    "at": now_iso(), "after_route_marker": True,
                    "module": final_runtime.get("current_module"),
                    "flow_status": final_runtime.get("flow_status"),
                    "row_version": final_runtime.get("row_version"),
                    "last_transition_reason": final_runtime.get("last_transition_reason"),
                    "active_goal_id": final_runtime.get("active_goal_id"),
                    "active_cycle_id": final_runtime.get("active_cycle_id"),
                    "conversation_next_module": latest_conversation.get("next_module"),
                })
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.75)
        return latest_program, latest_conversation, observations

    def say(self, message: str, *, provider: str) -> dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("conversation has not been created")
        started = time.perf_counter()
        payload = {
            "session_id": self.session_id,
            "message": message,
            "provider": provider,
            "generation_id": str(uuid.uuid4()),
        }
        attempts = []
        for attempt_number in range(1, 4):
            attempt_started = time.perf_counter()
            response = self.request("POST", "/api/chat", json=payload)
            ok = 200 <= response.status_code < 300
            error = None if ok else response.text[:1000].replace("\n", " ")
            attempts.append({
                "attempt": attempt_number,
                "status": response.status_code,
                "error": error,
                "elapsed_ms": round((time.perf_counter() - attempt_started) * 1000),
            })
            # Retry only explicit upstream connectivity failures. Validation,
            # integrity and other business failures remain visible immediately.
            retryable = (response.status_code == 502
                and bool(re.search(r"Could not reach[^\r\n]{0,160}API|request timed out", error or "", re.I))
                and not re.search(r"validator|validation|integrity|constraint|校验|完整性", error or "", re.I))
            if not retryable or attempt_number == 3:
                break
            time.sleep(2)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        transport = {
            "attempts": attempts,
            "retry_count": len(attempts) - 1,
            "failed_attempt_count": sum(not 200 <= item["status"] < 300 for item in attempts),
            "recovered_after_retry": ok and len(attempts) > 1,
        }
        if not ok:
            return {
                "ok": False,
                "http_status": response.status_code,
                "error": response.text[:1000].replace("\n", " "),
                "elapsed_ms": elapsed_ms,
                "message": message,
                **transport,
            }
        try:
            data = response.json()
        except ValueError:
            data = {"reply": response.text}
        return {"ok": True, "http_status": response.status_code, "elapsed_ms": elapsed_ms, "message": message, "response": data, **transport}


def transport_counts(result: dict[str, Any]) -> dict[str, int]:
    """Report recovery separately from workflow success; never hide failures."""
    turns = result.get("turns") or []
    return {
        "retry_count": sum(turn.get("retry_count", 0) for turn in turns),
        "failed_attempt_count": sum(turn.get("failed_attempt_count", int(not turn.get("ok", True))) for turn in turns),
        "recovered_turn_count": sum(bool(turn.get("recovered_after_retry")) for turn in turns),
    }


def classify(data: dict[str, Any]) -> str:
    attempts = data.get("turns", [])
    modules = [t.get("after", {}).get("runtime", {}).get("current_module") for t in attempts]
    runtime = data.get("final_program", {}).get("runtime", {})
    if data.get("full_cycle_completed"):
        return "cycle_completed"
    if any(not t.get("ok") for t in attempts):
        return "request_failure"
    if runtime.get("current_module") in {"module_1", "module_2", "module_3", "module_4"}:
        return "bounded_stall:" + str(runtime.get("current_module"))
    return "turn_budget_exhausted"


def strict_cycle_completed(scenario: Scenario, *, m4_seen: bool,
                           history: dict[str, Any] | None,
                           overview: dict[str, Any] | None) -> tuple[bool, dict[str, Any]]:
    """Require a confirmed M4 review and the scenario's real successor shape.

    Merely reaching M4, getting ``flow_status=completed`` or seeing one
    completed-looking row is insufficient.  The history API is authoritative:
    the reviewed cycle must be terminal and its review action must agree with
    the persona.  Continue/adjust additionally need a same-goal successor;
    adjust must leave a newer plan version instead of rewriting the old one.
    """
    evidence: dict[str, Any] = {"m4_seen": m4_seen, "reason": "not_checked"}
    if not m4_seen:
        evidence["reason"] = "module_4_not_reached"
        return False, evidence
    history = history or {}
    cycles = list(history.get("cycles") or [])
    plans = list(history.get("plans") or [])
    goal = history.get("goal") or {}
    # In the v2 schema a cycle is terminal only after the confirmed review has
    # moved it to ``completed``.  ``confirmed`` belongs to records/reviews,
    # and ``closed`` is not a legal pa_cycles status; accepting either would
    # let a plan confirmation masquerade as a completed M4 cycle.
    terminal_statuses = {"completed"}
    terminal = [c for c in cycles if c.get("status") in terminal_statuses and c.get("review_status") == "confirmed"]
    reviewed = [c for c in terminal if c.get("review_action") == scenario.decision]
    evidence.update({"goal_status": goal.get("status"), "cycle_statuses": [c.get("status") for c in cycles],
                     "review_actions": [c.get("review_action") for c in cycles],
                     "plan_versions": [p.get("version_no") for p in plans],
                     "terminal_review_count": len(reviewed)})
    if not reviewed:
        evidence["reason"] = "no_confirmed_terminal_cycle_with_expected_action"
        return False, evidence
    reviewed_cycle = max(reviewed, key=lambda row: (row.get("ordinal") or 0, str(row.get("id"))))
    if scenario.decision in {"end", "pause"}:
        expected_goal = "completed" if scenario.decision == "end" else "paused"
        latest_ordinal = max((c.get("ordinal") or 0) for c in cycles) if cycles else 0
        ok = goal.get("status") == expected_goal and (reviewed_cycle.get("ordinal") or 0) == latest_ordinal
        evidence["latest_ordinal"] = latest_ordinal
        evidence["reason"] = "ok" if ok else "goal_status_mismatch"
        return ok, evidence
    successors = [c for c in cycles if c.get("id") != reviewed_cycle.get("id") and (c.get("ordinal") or 0) > (reviewed_cycle.get("ordinal") or 0)]
    if not successors:
        evidence["reason"] = "missing_same_goal_successor_cycle"
        return False, evidence
    successor = max(successors, key=lambda row: (row.get("ordinal") or 0, str(row.get("id"))))
    evidence["successor"] = {"id": successor.get("id"), "ordinal": successor.get("ordinal"), "status": successor.get("status")}
    if goal.get("status") != "active":
        evidence["reason"] = "successor_goal_not_active"
        return False, evidence
    if scenario.decision == "continue":
        # Continuing keeps the exact confirmed plan version while opening a
        # new cycle.  A successor pointing at a different version is an
        # adjustment masquerading as continue.
        ok = (successor.get("status") in {"waiting_execution", "reviewing", "planning"}
              and successor.get("plan_version") == reviewed_cycle.get("plan_version"))
        evidence["reviewed_plan_version"] = reviewed_cycle.get("plan_version")
        evidence["successor_plan_version"] = successor.get("plan_version")
        evidence["reason"] = "ok" if ok else "continue_successor_not_open"
        return ok, evidence
    # Adjust must retain the historical confirmed plan and create a newer
    # editable version.  This makes an old-card confirmation impossible to
    # count as a successful re-plan.
    confirmed_versions = [int(p["version_no"]) for p in plans if p.get("record_status") == "confirmed" and p.get("version_no") is not None]
    draft_versions = [int(p["version_no"]) for p in plans if p.get("record_status") == "draft" and p.get("version_no") is not None]
    expected_duration = scenario.expected_next_duration or 5
    ok = (successor.get("status") == "planning" and bool(confirmed_versions) and bool(draft_versions)
          and max(draft_versions) > max(confirmed_versions)
          and any(int(p.get("duration_minutes") or 0) == expected_duration and p.get("record_status") == "draft" for p in plans))
    evidence.update({"confirmed_versions": confirmed_versions, "draft_versions": draft_versions,
                     "expected_next_duration": expected_duration,
                     "draft_durations": [p.get("duration_minutes") for p in plans if p.get("record_status") == "draft"]})
    evidence["reason"] = "ok" if ok else "adjust_successor_plan_version_invalid"
    return ok, evidence


def self_check_strict_oracle() -> None:
    """Guard the re-plan oracle against accidentally accepting the old card.

    This uses only in-memory history and performs no HTTP request.  The
    re-plan personas deliberately execute the M2 five-minute card, then ask
    M4 to create a three-minute successor; a stale five-minute draft must not
    pass the strict check.
    """
    scenario = next(item for item in SCENARIOS if item.name == "partial_adjust_replan")
    history = {
        "goal": {"status": "active"},
        "plans": [
            {"version_no": 1, "record_status": "confirmed", "duration_minutes": 5},
            {"version_no": 2, "record_status": "draft", "duration_minutes": 5},
        ],
        "cycles": [
            {"id": "cycle-1", "ordinal": 1, "status": "completed", "review_status": "confirmed", "review_action": "adjust", "plan_version": 1},
            {"id": "cycle-2", "ordinal": 2, "status": "planning", "review_status": "draft", "review_action": None, "plan_version": 2},
        ],
    }
    accepted, _ = strict_cycle_completed(scenario, m4_seen=True, history=history, overview={})
    if accepted:
        raise AssertionError("strict adjust oracle accepted stale five-minute draft for expected three-minute successor")
    history["plans"][1]["duration_minutes"] = 3
    accepted, _ = strict_cycle_completed(scenario, m4_seen=True, history=history, overview={})
    if not accepted:
        raise AssertionError("strict adjust oracle rejected expected three-minute successor")


def run_scenario(scenario: Scenario, *, base: str, provider: str, settle_seconds: float, max_turns: int) -> dict[str, Any]:
    client = Client(base=base, scenario=scenario)
    scenario_metadata = dict(scenario.__dict__)
    # Keep the expected successor duration explicit for every report row;
    # only the re-plan personas override the normal scenario duration.
    scenario_metadata["expected_next_duration"] = (
        scenario.expected_next_duration if scenario.expected_next_duration is not None else scenario.duration
    )
    result: dict[str, Any] = {
        "scenario": scenario_metadata,
        "started_at": now_iso(),
        "base_url": base,
        "provider": provider,
        "turns": [],
        "m4_seen": False,
        "m4_dialogue": {"rounds": 0, "duplicate_rounds": 0, "followup_rounds": 0},
        "read_only_endpoints": ["GET /api/program/{session_id}", "GET /api/conversations/{session_id}", "GET /api/program/goals/overview", "GET /api/program/goals/{goal_id}/history"],
    }
    try:
        client.register()
        result["username"] = client.username
        initial = client.new_conversation()
        result["session_id"] = client.session_id
        result["initial"] = {"message_count": len(initial.get("messages") or []), "next_module": initial.get("next_module")}
        scripts = messages_for(scenario)
        counts = {module: 0 for module in scripts}
        module = (client.program().get("runtime") or {}).get("current_module") or "module_1"
        seen_goal: str | None = None
        seen_cycle: str | None = None
        history: dict[str, Any] | None = None
        m4_state: dict[str, Any] = {
            "rounds": 0, "duplicate_rounds": 0, "followup_rounds": 0,
            "messages": [], "actor_reasons": [],
        }
        successor_prepared = False

        def checkpoint(row: dict[str, Any], *, current_module: str) -> None:
            """Persist and print a redacted per-turn checkpoint.

            ``result`` contains only chat responses and public read-state; the
            registration bearer token is intentionally never copied into it.
            """
            snapshot = dict(result)
            snapshot["partial"] = True
            snapshot["counts"] = dict(counts)
            snapshot["last_module"] = current_module
            snapshot.update(transport_counts(result))
            save_json(scenario_path(scenario), snapshot)
            after_runtime = (row.get("after") or {}).get("runtime") or {}
            print(json.dumps({
                "event": "turn",
                "scenario": scenario.name,
                "turn": row.get("turn"),
                "ok": row.get("ok"),
                "elapsed_ms": row.get("elapsed_ms"),
                "retry_count": row.get("retry_count", 0),
                "failed_attempt_count": row.get("failed_attempt_count", 0),
                "module_before": row.get("module_before"),
                "module_after": after_runtime.get("current_module"),
                "flow_status": after_runtime.get("flow_status"),
                "missing_fields": (row.get("after") or {}).get("missing_fields") or [],
                "active_goal": after_runtime.get("active_goal_id"),
                "active_cycle": after_runtime.get("active_cycle_id"),
                "actor_reason": row.get("actor_reason"),
                "m4_round": row.get("m4_round"),
            }, ensure_ascii=False), flush=True)

        for turn_number in range(1, max_turns + 1):
            # An adjust review opens a new editable M2 cycle.  The old cycle's
            # five-minute card must be changed and reconfirmed before the
            # successor can pass the strict three-minute oracle; do not reuse
            # the first-cycle script, which would accidentally reconfirm the
            # stale card.
            if (scenario.replan and module == "module_2" and not successor_prepared
                    and history and len(history.get("cycles") or []) > 1):
                scripts["module_2"] = [
                    "我想把每次时长从5分钟改成3分钟，其他安排不变。请按修改后的计划重新整理，我会再确认。",
                    "修改后的活动、时间、地点、频率、障碍和应对都清楚，也符合我能做到的程度。",
                    "我确认修改后的计划：每天晚上在原地点做3分钟，遇到原来的障碍按刚才的应对办法处理，就这样执行。",
                    "我看过刚才重新整理的完整三分钟计划，愿意按这份安排执行。",
                ]
                counts["module_2"] = 0
                counts["module_3"] = 0
                successor_prepared = True
            if module not in scripts:
                result["stop_reason"] = "no_script_for_" + str(module)
                break
            if module == "module_4" and m4_state["rounds"] >= 10:
                result["stop_reason"] = "m4_adaptive_round_limit"
                break
            if module != "module_4" and counts.get(module, 0) >= len(scripts[module]):
                result["stop_reason"] = "no_script_for_" + str(module)
                break
            # Capture the previous assistant row so settle() cannot mistake
            # an older routed message for this turn's completion marker.
            try:
                before_program = client.program()
                before_detail = client.conversation()
                before_assistants = [m for m in (before_detail.get("messages") or []) if m.get("role") == "assistant"]
                previous_assistant_id = before_assistants[-1].get("id") if before_assistants else None
            except Exception:
                before_program = {}
                before_detail = {}
                previous_assistant_id = None
            if module == "module_4":
                text, actor_reason = adaptive_m4_message(
                    scenario, before_program, before_detail,
                    round_no=m4_state["rounds"] + 1,
                    initial_message=scripts[module][0],
                )
                m4_state["rounds"] += 1
                if _compact_text(text) in {_compact_text(item) for item in m4_state["messages"]}:
                    m4_state["duplicate_rounds"] += 1
                if actor_reason != "m4_initial_execution_feedback":
                    m4_state["followup_rounds"] += 1
                m4_state["messages"].append(text)
                m4_state["actor_reasons"].append(actor_reason)
                result["m4_dialogue"] = {
                    "rounds": m4_state["rounds"],
                    "duplicate_rounds": m4_state["duplicate_rounds"],
                    "followup_rounds": m4_state["followup_rounds"],
                    "actor_reasons": list(m4_state["actor_reasons"]),
                }
            else:
                text = scripts[module][counts[module]]
                actor_reason = None
            counts[module] += 1
            call = client.say(text, provider=provider)
            row: dict[str, Any] = {"turn": turn_number, "module_before": module, "script_index": counts[module] - 1, **call}
            if actor_reason is not None:
                row.update({"actor_reason": actor_reason, "m4_round": m4_state["rounds"]})
            if not call.get("ok"):
                result["turns"].append(row)
                checkpoint(row, current_module=module)
                result["stop_reason"] = "request_failure"
                break
            try:
                after, conversation, observations = client.settle(
                    module, settle_seconds, previous_assistant_id=previous_assistant_id)
            except Exception as exc:  # read-side failure remains in evidence
                row["settle_error"] = type(exc).__name__ + ": " + str(exc)
                after, conversation, observations = {}, {}, []
            runtime = after.get("runtime") or {}
            if module == "module_4":
                result["m4_seen"] = True
            row["after"] = after
            row["conversation_after"] = {"revision": conversation.get("revision"), "next_module": conversation.get("next_module")}
            row["settle_observations"] = observations
            row["reply_preview"] = ((call.get("response") or {}).get("reply") or "")[:500]
            result["turns"].append(row)
            seen_goal = seen_goal or runtime.get("active_goal_id")
            seen_cycle = seen_cycle or runtime.get("active_cycle_id")
            if runtime.get("active_goal_id"):
                try:
                    history = client.json_api("GET", "/api/program/goals/" + runtime["active_goal_id"] + "/history")
                    row["history_structure"] = {
                        "goal_status": (history.get("goal") or {}).get("status"),
                        "plan_versions": [p.get("version_no") for p in history.get("plans", [])],
                        "plan_statuses": [p.get("record_status") for p in history.get("plans", [])],
                        "cycle_ordinals": [c.get("ordinal") for c in history.get("cycles", [])],
                        "cycle_statuses": [c.get("status") for c in history.get("cycles", [])],
                        "review_statuses": [c.get("review_status") for c in history.get("cycles", [])],
                        "review_actions": [c.get("review_action") for c in history.get("cycles", [])],
                    }
                except Exception as exc:
                    row["history_error"] = type(exc).__name__ + ": " + str(exc)
            module = runtime.get("current_module") or module
            cycle_ok, cycle_evidence = strict_cycle_completed(
                scenario, m4_seen=bool(result.get("m4_seen")),
                history=history, overview=client.overview())
            row["cycle_evidence"] = cycle_evidence
            checkpoint(row, current_module=module)
            if cycle_ok:
                result["full_cycle_completed"] = True
                result["stop_reason"] = "cycle_completed"
                break
        else:
            result["stop_reason"] = "turn_budget_exhausted"
        result["counts"] = counts
        result["final_program"] = client.program()
        result["final_overview"] = client.overview()
        if seen_goal:
            try:
                result["final_history"] = client.json_api("GET", "/api/program/goals/" + seen_goal + "/history")
            except Exception as exc:
                result["final_history_error"] = type(exc).__name__ + ": " + str(exc)
        result["seen_goal_id"] = seen_goal
        result["seen_cycle_id"] = seen_cycle
        result["full_cycle_completed"], result["cycle_evidence"] = strict_cycle_completed(
            scenario, m4_seen=bool(result.get("m4_seen")), history=history,
            overview=result["final_overview"])
        if result["full_cycle_completed"]:
            result["stop_reason"] = "cycle_completed"
        result["status"] = classify(result)
    except Exception as exc:
        result["status"] = "setup_failure"
        result["error"] = type(exc).__name__ + ": " + str(exc)
    result["finished_at"] = now_iso()
    result.update(transport_counts(result))
    return result


def write_report(results: list[dict[str, Any]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    totals = {key: sum(transport_counts(row)[key] for row in results)
              for key in ("retry_count", "failed_attempt_count", "recovered_turn_count")}
    m4_totals = {
        "m4_duplicate_rounds": sum((row.get("m4_dialogue") or {}).get("duplicate_rounds", 0) for row in results),
        "m4_followup_rounds": sum((row.get("m4_dialogue") or {}).get("followup_rounds", 0) for row in results),
    }
    save_json(OUT / "summary.json", {
        "generated_at": now_iso(),
        "scope": "local HTTP black-box; fresh account per scenario; no forced module or confirmation writes",
        "base_url": results[0].get("base_url") if results else DEFAULT_BASE,
        "count": len(results),
        "status_counts": {status: sum(1 for row in results if row.get("status") == status) for status in sorted({row.get("status") for row in results})},
        "cycle_completed": sum(bool(row.get("full_cycle_completed")) for row in results),
        **totals,
        **m4_totals,
        "results": [{
            "scenario": row.get("scenario", {}).get("name"), "username": row.get("username"),
            "session_id": row.get("session_id"), "status": row.get("status"),
            "stop_reason": row.get("stop_reason"), "seen_goal_id": row.get("seen_goal_id"),
            "seen_cycle_id": row.get("seen_cycle_id"), "turn_count": len(row.get("turns", [])),
            "final_module": (row.get("final_program", {}).get("runtime") or {}).get("current_module"),
            "final_flow_status": (row.get("final_program", {}).get("runtime") or {}).get("flow_status"),
            "m4_seen": row.get("m4_seen", False),
            "expected_next_duration": row.get("scenario", {}).get("expected_next_duration"),
            "m4_dialogue": row.get("m4_dialogue"),
            "cycle_evidence_reason": (row.get("cycle_evidence") or {}).get("reason"),
            **transport_counts(row),
        } for row in results],
    })
    report_lines = [
        "# 本地 M1→M4 周期调试报告", "",
        "本报告由本地 HTTP 黑盒脚本生成。每个场景使用独立新账号和新会话，聊天只调用普通 `/api/chat`；脚本没有传 `module`，没有调用确认/选目标写入接口，也没有直接修改数据库。状态接口全部为 GET。", "",
        f"生成时间：{now_iso()}", f"场景数：{len(results)}", f"完整闭环数：{sum(bool(row.get('full_cycle_completed')) for row in results)}",
        f"额外重试次数（retry_count）：{totals['retry_count']}",
        f"失败请求次数（failed_attempt_count）：{totals['failed_attempt_count']}",
        f"重试后恢复的对话轮数：{totals['recovered_turn_count']}；恢复后的闭环不代表全程无错误。", "",
        f"M4 补问轮数：{m4_totals['m4_followup_rounds']}；重复话术轮数：{m4_totals['m4_duplicate_rounds']}。", "",
        "| 场景 | 状态 | 严格闭环判定 | 停止原因 | 最终模块 | 最终流程 | 轮数 | M4补问 | M4重复 | 重试 | 失败请求 |", "|---|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        rt = row.get("final_program", {}).get("runtime") or {}
        counts = transport_counts(row)
        m4 = row.get("m4_dialogue") or {}
        report_lines.append(f"| {row.get('scenario', {}).get('name')} | {row.get('status')} | {(row.get('cycle_evidence') or {}).get('reason', '')} | {row.get('stop_reason', '')} | {rt.get('current_module', '')} | {rt.get('flow_status', '')} | {len(row.get('turns', []))} | {m4.get('followup_rounds', 0)} | {m4.get('duplicate_rounds', 0)} | {counts['retry_count']} | {counts['failed_attempt_count']} |")
    report_lines += ["", "## 判定说明", "", "- `cycle_completed`：只在服务端只读状态或目标历史显示周期已完成时记录。", "- `bounded_stall:module_N`：脚本用尽该模块的自然话术，仍未观察到合法跳转。", "- `request_failure`：普通聊天请求返回非 2xx；响应摘要保存在对应场景 JSON。", "- `setup_failure`：账号或会话建立失败。", "", "逐轮原始证据见同目录各场景 JSON。"]
    (OUT / "REPORT.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")


def scenario_path(scenario: Scenario) -> Path:
    """Return the batch-isolated evidence path for one scenario."""
    return OUT / f"scenario-{scenario.number:02d}-{scenario.name}.json"


def main() -> int:
    global OUT
    self_check_strict_oracle()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["pilot", "batch"])
    parser.add_argument("--scenario", type=int, default=1, help="pilot scenario number, 1-10")
    parser.add_argument("--count", type=int, default=10, help="batch scenarios from 1 onward")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER, choices=["deepseek", "claude", "doubao"])
    parser.add_argument("--settle-seconds", type=float, default=60.0)
    parser.add_argument("--max-turns", type=int, default=42)
    parser.add_argument("--workers", type=int, default=1, help="parallel independent scenarios (1-3; default 1)")
    parser.add_argument("--out", default=str(OUT), help="isolated evidence directory for this batch")
    args = parser.parse_args()
    base = require_local_base(args.base_url)
    if not 1 <= args.workers <= 3:
        parser.error("--workers must be between 1 and 3")
    OUT = Path(args.out).expanduser().resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.action == "pilot":
        if not 1 <= args.scenario <= len(SCENARIOS):
            parser.error("--scenario must be between 1 and 10")
        selected = [SCENARIOS[args.scenario - 1]]
    else:
        if not 1 <= args.count <= len(SCENARIOS):
            parser.error("--count must be between 1 and 10")
        selected = SCENARIOS[:args.count]
    results: list[dict[str, Any]] = []

    def run_one(scenario: Scenario) -> dict[str, Any]:
        print(json.dumps({"event": "start", "scenario": scenario.name, "base_url": base, "out": str(OUT)}, ensure_ascii=False), flush=True)
        result = run_scenario(scenario, base=base, provider=args.provider, settle_seconds=args.settle_seconds, max_turns=args.max_turns)
        # The per-turn checkpoints are written inside run_scenario.  This final
        # write includes the terminal summary even when the worker is concurrent.
        save_json(scenario_path(scenario), result)
        runtime = result.get("final_program", {}).get("runtime") or {}
        print(json.dumps({"event": "finish", "scenario": scenario.name, "status": result.get("status"), "stop_reason": result.get("stop_reason"), "module": runtime.get("current_module"), "flow_status": runtime.get("flow_status"), "turns": len(result.get("turns", []))}, ensure_ascii=False), flush=True)
        return result

    if args.workers == 1 or len(selected) == 1:
        results = [run_one(scenario) for scenario in selected]
    else:
        ordered: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="local-cycle") as pool:
            futures = {pool.submit(run_one, scenario): scenario for scenario in selected}
            for future in as_completed(futures):
                scenario = futures[future]
                try:
                    ordered[scenario.number] = future.result()
                except Exception as exc:
                    # A worker-level failure still gets a report row and does
                    # not prevent the other independent accounts finishing.
                    ordered[scenario.number] = {
                        "scenario": scenario.__dict__, "base_url": base,
                        "status": "worker_failure",
                        "error": type(exc).__name__ + ": " + str(exc),
                        "turns": [], "finished_at": now_iso(),
                    }
        results = [ordered[number] for number in sorted(ordered)]
    write_report(results)
    print(json.dumps({"report": str(OUT / "REPORT.md"), "summary": str(OUT / "summary.json"), "count": len(results), "completed": sum(bool(r.get("full_cycle_completed")) for r in results)}, ensure_ascii=False), flush=True)
    return 0 if all(r.get("status") == "cycle_completed" for r in results) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
