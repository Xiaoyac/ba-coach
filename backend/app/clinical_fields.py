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
    Spec("chief_complaint", "text", "用户最新明确的主要困扰或希望改善之处，不诊断、不推测动机"),
    Spec("distress_duration", "varchar", "困扰持续多久，如「三个月」「大半年」", 64),
    Spec("distress_frequency", "varchar", "出现频率，如「几乎每天」「每周两三次」", 64),
    Spec("trigger_situation", "text", "用户报告的同一个具体事件情境，不混合多个事件；低披露或未知为 null"),
    Spec(
        "abc_event",
        "json",
        '单个具体事件的还原，对象格式：'
        '{"trigger":"发生了什么事","feeling":"主要感受、情绪或状态","thought":"可选想法",'
        '"behavior":"实际行动或不行动","consequence":"实际结果"}；未谈到的键给 null。'
        '这是事件快照，不是固定 B=信念的 CBT 模型；身体感受和想法可选。纠正时整件事保持一致；不得编造',
    ),
    Spec("coping_behavior", "text", "同一事件实际行动或不行动，不是未来计划；刷手机、躺着不等于回避"),
    Spec("coping_consequence", "text", "同一事件的实际反馈；没有明显变化也有效，不写假设结果或推断因果"),
    Spec("ai_depression_cycle_summary", "text", "逻辑名 behavior_state_relation_summary：教练让用户核对的当前具体事件行为—状态/结果关系原文，完整连续逐字提取，与m1_contract.summary_quote一致。不要用后续一般性BA教育或收尾回顾替换已获认可的事件总结；只有事实被纠正才更新并重新确认。不是必须存在抑郁循环，机制标签需要额外的重复证据"),
    Spec("user_approval_level", "level", f"仅对最新关系总结的认可，非 BA 理解或目标意愿；纠正使旧认可失效，{LEVEL_SCALE}"),
    Spec(
        "attempted_relief_methods",
        "json",
        "简单字符串数组，保留尝试过/正在做/想到过及实际效果；null=未知或不愿谈，[]=用户明确没有，禁止把未提及写成 []",
    ),
    Spec(
        "exception_positive_scene",
        "json",
        "用户实际提到的例外情境及结果，字符串数组；未提及给 null，不编造，不作为完成门槛",
    ),
)

MODULE_TWO: tuple[Spec, ...] = (
    Spec("pa_understanding_level", "level", f"用户对 PA（身体活动）概念的理解程度，{LEVEL_SCALE}"),
    Spec("pa_approval_level", "level", f"用户对尝试 PA 的接受程度，{LEVEL_SCALE}"),
    Spec("core_values", "text", "用户在对话中表达的核心价值观"),
    Spec("core_values_impact", "text", "该价值观如何影响其活动选择"),
    Spec("target_activity_content", "varchar", "目标活动做什么（What）", 255),
    Spec("schedule_text", "varchar", "用户确认的自然语言安排，优先逐字引用包含日期/星期和开始时间的用户原话，不改写或擅自补全；如每周三晚饭后。不要只因无法转换日期而丢弃，未知为 null", 255),
    Spec(
        "target_activity_time",
        "datetime",
        '计划执行时间，ISO 格式 "YYYY-MM-DD HH:MM:SS"；只说了「明天下午」这类模糊时间就给 null',
    ),
    Spec("target_activity_location", "varchar", "在哪里做（Where）", 255),
    Spec("target_activity_duration_minutes", "int", "计划时长，单位分钟，整数"),
    Spec("target_activity_companion", "varchar", "和谁一起（Who），独自完成写「独自」", 32),
    Spec("frequency_rule", "json", '明确表达的执行频率或一次性安排，{"schema_version":1,"text":"每周三次"}；“今天试一次”“先做一次”也要原样记录，不得从单独日期推断，未知为 null'),
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
    # This field is also the structured snapshot shown in the confirmation
    # card.  It must be populated for a complete *proposal* before consent;
    # otherwise the first M3 card cannot be rendered or versioned and the
    # user's next natural confirmation has nothing authoritative to bind to.
    # The record becomes final only when the user's confirmation is committed.
    Spec("negotiated_record_plan", "text", "当前对话中完整、可执行的拟议记录方式与格式；用户确认后才视为最终商定"),
    Spec("has_contract_reached", "flag", "是否已就执行契约达成一致，true 或 false"),
    Spec("difficulty_feedback_mechanism", "text", "约定的遇到困难时的反馈机制"),
)

MODULE_FOUR: tuple[Spec, ...] = (
    Spec("scenario_type", "varchar", "仅当前焦点目标：A 实际PA且情绪改善；B 尚未开始且明确执行前障碍；C 执行窗口已过且排除A/B；信息不足null，服务器另核验证据", 8),
    Spec(
        "execution_result",
        "int",
        "当前周期焦点 PA 客观执行：1=按计划完成活动，2=未开始无明确障碍，3=未开始有明确障碍，4=部分完成。情绪效果单独记scenario_type，不把已完成但无情绪改善记为未执行",
    ),
    Spec(
        "phase_a",
        "json",
        '前因对象：schema_version=1，situation、trigger、physical_state、emotion、hindering_factors（原话数组）。未知null，用户明确无障碍才[]；仅本次实际经历，不从结果倒推',
    ),
    Spec(
        "phase_b",
        "json",
        '行为对象：schema_version=1，overt={activity:实际活动名, actual_duration_minutes:实际分钟数或null, action_taken:是否实际开始的布尔值, completion_status:complete/partial/not_started}，covert（有证据的内隐行为）、coping_method。未开始时actual_duration_minutes可0；不得用计划时长补实际时长',
    ),
    Spec(
        "phase_c",
        "json",
        '后果对象：schema_version=1，short_term={emotion_change:用户实际观察的变化}，long_term={action_willingness:已表达的后续意愿或null}，functional_analysis（有依据的功能分析）。未知null，不预测长期效果',
    ),
    Spec("ai_abc_chain_summary", "text", "逻辑名abc_chain_summary：教练给用户核对的A→B→C事件分析原文，完整连续逐字引用。取用户认可之前的该段分析，不要选末尾的复盘收尾(review_summary)。收尾提到ABC已核对不代表提出了新的ABC分析；只有事实/解释被纠正才用修正后的ABC"),
    Spec("user_chain_approval_level", "level", f"用户对该 ABC 分析的认可程度，{LEVEL_SCALE}"),
    Spec("core_difficulty_type", "varchar", "核心困难类型，如「动机不足」「环境阻碍」「计划过难」", 128),
    Spec("difficulty_description", "text", "困难的具体描述"),
    Spec("ba_reeducation_content", "text", "ABC获用户核对后实际向用户提供的情境化BA教育/强化原文，不是准备提供的知识"),
    Spec("next_coping_strategy", "text", "ABC核对及BA教育后，用户参与形成的继续原目标策略；不得修改活动内容/时长，调整转M2时不强填"),
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
