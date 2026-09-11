"""Declarative spec of what each module contributes to the business tables.

One source of truth for two things that must never drift apart:

* the prompt that asks the model to extract these fields, and
* the validation that decides what is allowed to reach the database.

Keeping them in separate files is how an extractor ends up cheerfully emitting
a key nothing writes, or writing a 900-character string into a VARCHAR(255)
and taking the whole transaction down with it. Here the prompt is *generated*
from the same tuples the validator walks.

Everything a model produces is untrusted input. `kind` and `max_length` below
are not documentation — `clinical_store.coerce` enforces them, drops unknown
keys outright, and truncates rather than letting MySQL raise. The column types
are those in `models_business.py`, which were read back from the live schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FieldKind = Literal["text", "varchar", "json", "level", "flag", "datetime", "int"]


@dataclass(frozen=True)
class Spec:
    """One extractable column."""

    name: str
    kind: FieldKind
    # Shown to the model as the instruction for this field. Written in Chinese
    # because the conversation is, and a Chinese instruction next to Chinese
    # source text measurably reduces the model answering in the wrong language.
    prompt: str
    max_length: int | None = None


# The live business schema defines every agreement/understanding level as
# 0=not accepted/understood, 1=partial, 2=accepted/understood.  Keep the model
# prompt and the write validator on that exact semantic scale.
LEVEL_SCALE = "0-2 的整数（0=不认可/不理解，1=部分认可/理解，2=认可/理解），无法判断时给 null"


MODULE_ONE: tuple[Spec, ...] = (
    Spec("chief_complaint", "text", "用户此次求助的核心困扰，一到两句话"),
    Spec("distress_duration", "varchar", "困扰持续多久，如「三个月」「大半年」", 64),
    Spec("distress_frequency", "varchar", "出现频率，如「几乎每天」「每周两三次」", 64),
    Spec("trigger_situation", "text", "触发困扰的典型情境"),
    Spec(
        "abc_event",
        "json",
        '单个具体事件的还原，对象格式：'
        '{"trigger":"发生了什么事","feeling":"情绪与躯体感受","thought":"当时的想法",'
        '"behavior":"做了什么","consequence":"结果如何"}；未谈到的键给 null',
    ),
    Spec("coping_behavior", "text", "用户面对困扰时采取的应对行为"),
    Spec("coping_consequence", "text", "该应对行为带来的后果"),
    Spec("ai_depression_cycle_summary", "text", "教练向用户复述的抑郁循环总结"),
    Spec("user_approval_level", "level", f"用户对上述循环总结的认可程度，{LEVEL_SCALE}"),
    Spec(
        "attempted_relief_methods",
        "json",
        "用户已经尝试过的缓解方法，字符串数组；没有提到就给 []",
    ),
    Spec(
        "exception_positive_scene",
        "json",
        "用户提到的例外/正向情境（心情较好或做成某事的时刻），字符串数组；没有就给 []",
    ),
)

MODULE_TWO: tuple[Spec, ...] = (
    Spec("pa_understanding_level", "level", f"用户对 PA（身体活动）概念的理解程度，{LEVEL_SCALE}"),
    Spec("pa_approval_level", "level", f"用户对尝试 PA 的接受程度，{LEVEL_SCALE}"),
    Spec("core_values", "text", "用户在对话中表达的核心价值观"),
    Spec("core_values_impact", "text", "该价值观如何影响其活动选择"),
    Spec("target_activity_content", "varchar", "目标活动做什么（What）", 255),
    Spec("schedule_text", "varchar", "用户确认的自然语言安排，如每周三晚饭后；不要只因无法转换日期而丢弃，未知为 null", 255),
    Spec(
        "target_activity_time",
        "datetime",
        '计划执行时间，ISO 格式 "YYYY-MM-DD HH:MM:SS"；只说了「明天下午」这类模糊时间就给 null',
    ),
    Spec("target_activity_location", "varchar", "在哪里做（Where）", 255),
    Spec("target_activity_duration_minutes", "int", "计划时长，单位分钟，整数"),
    Spec("target_activity_companion", "varchar", "和谁一起（Who），独自完成写「独自」", 32),
    Spec("frequency_rule", "json", '明确表达的执行频率，{"schema_version":1,"text":"每周三次"}，不得推断，未知为 null'),
    Spec("potential_barriers", "json", "可能遇到的障碍，字符串数组；没谈到给 []；只有用户明确表示无障碍时记录该原意，不得把未知写成无障碍"),
    Spec(
        "barrier_coping_plan",
        "json",
        '针对障碍的应对方案，数组，每项 {"barrier":"障碍","plan":"应对"}；没谈到给 []',
    ),
    Spec(
        "has_target_card_generated",
        "flag",
        "本轮是否已经产出完整的 PA 目标卡片（What/When/Where/How/Who 齐备），true 或 false",
    ),
)

MODULE_THREE: tuple[Spec, ...] = (
    Spec("ai_record_requirement", "text", "教练向用户说明的记录要求"),
    Spec("user_acceptance_level", "level", f"用户对记录要求的接受程度，{LEVEL_SCALE}"),
    Spec("user_acceptance_feeling", "text", "用户对记录这件事表达的感受"),
    Spec("negotiated_record_plan", "text", "双方最终商定的记录方式与格式"),
    Spec("has_contract_reached", "flag", "是否已就执行契约达成一致，true 或 false"),
    Spec("difficulty_feedback_mechanism", "text", "约定的遇到困难时的反馈机制"),
)

MODULE_FOUR: tuple[Spec, ...] = (
    Spec(
        "execution_result",
        "int",
        "本次 PA 目标的执行结果：1=执行成功，2=未执行，3=执行受阻，4=部分完成；无法判断给 null",
    ),
    Spec(
        "phase_a",
        "json",
        '前因（Antecedent）：{"situation":"当时的情境","trigger":"触发因素","state":"执行前的状态"}',
    ),
    Spec(
        "phase_b",
        "json",
        '行为（Behavior）：{"action":"实际做了什么","deviation":"与计划的差异"}',
    ),
    Spec(
        "phase_c",
        "json",
        '后果（Consequence）：{"feeling":"事后感受","reward":"获得的奖赏","impact":"对情绪的影响"}',
    ),
    Spec("ai_abc_chain_summary", "text", "教练给出的 ABC 功能分析总结"),
    Spec("user_chain_approval_level", "level", f"用户对该 ABC 分析的认可程度，{LEVEL_SCALE}"),
    Spec("core_difficulty_type", "varchar", "核心困难类型，如「动机不足」「环境阻碍」「计划过难」", 128),
    Spec("difficulty_description", "text", "困难的具体描述"),
    Spec("ba_reeducation_content", "text", "本轮进行的 BA 行为再教育内容"),
    Spec("next_coping_strategy", "text", "下一步的应对策略"),
    Spec(
        "review_decision",
        "int",
        "复盘决定：1=继续原目标，2=更换目标，3=调整目标，4=结束目标；无法判断给 null",
    ),
    Spec("review_summary", "text", "本轮复盘总结"),
)

MODULE_SPECS: dict[str, tuple[Spec, ...]] = {
    "module_1": MODULE_ONE,
    "module_2": MODULE_TWO,
    "module_3": MODULE_THREE,
    "module_4": MODULE_FOUR,
}

# `risk_monitoring` is not a module's record — it is checked on every turn
# regardless of which module is running, because a risk signal does not wait
# for a module boundary.
RISK_SPECS: tuple[Spec, ...] = (
    Spec(
        "risk_status",
        "int",
        "本轮是否出现自伤/自杀/严重危机信号：0=无，1=有。绝大多数轮次都应是 0，"
        "只有用户明确表达伤害自己、不想活了、或具体计划时才给 1",
    ),
    Spec(
        "risk_expression_type",
        "int",
        "风险表达类型（严格按数据库编码）：1=自伤，2=自杀，3=自杀未遂；"
        "具体计划但尚未实施按 2 记录，并在 risk_context 中保留计划细节；risk_status=0 时给 null",
    ),
    Spec(
        "risk_context",
        "json",
        '风险出现的上下文：{"quote":"用户的原话（照抄，不要改写）","situation":"当时情境"}；'
        "risk_status=0 时给 null",
    ),
    Spec("user_reaction_to_risk", "varchar", "用户对教练回应的反应；risk_status=0 时给 null", 255),
)


def prompt_for(specs: tuple[Spec, ...]) -> str:
    """Render a spec tuple as the field list injected into an extraction prompt."""
    return "\n".join(f"- {s.name}: {s.prompt}" for s in specs)
