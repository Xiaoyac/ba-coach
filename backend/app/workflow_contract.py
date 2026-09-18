"""Versioned registry shared by routing, persistence, DMS and documentation."""
VERSION = "workflow-20260917-m4-v1"
STEP_REGISTRY = {
 "module_1": (
  ("core_problem_example", "具体经历或低披露选择", "milestone_1：同一事件的情境、主要体验、实际行为、实际结果齐全；用户明确不愿披露/分析可选择低披露，不用虚构补齐。"),
  ("depression_cycle_formulated", "行为与状态关系", "milestone_2：当前事实关系基本获认可，缓解方法已知有/无；不强求抑郁循环。低披露可跳过个人分析。更正使旧认可失效。"),
  ("ba_education_completed", "BA 教育与理解", "milestone_3 的理解门：五项 BA 教育完成，用户基本理解且无未解决核心疑问；低披露用一般解释也须确认理解。"),
  ("goal_setting_consent", "目标设定意愿", "用户明确愿意开始目标设定；沉默、模糊附和或对其他问题说继续不算。")),
 "module_2": (
  ("pa_concept_understood", "PA 理解", "用户理解 PA 是具体行动任务，与 BA 方法区分。"),
  ("values_or_intention_explored", "价值与意向", "已探索本轮活动的个人意义或行动意向，不强制预设价值。"),
  ("activity_selected", "活动选择", "用户选择具体活动，尚未选择的备选方案不算。"),
  ("pa_card_completed", "目标卡完成", "本轮活动、时间、地点、时长、频率、潜在障碍和应对方案完整可执行，且经用户确认。")),
 "module_3": (
  ("recording_explained", "记录说明", "已说明记录内容、用途和操作方式。"),
  ("recording_plan_agreed", "记录计划", "用户同意实际可行的记录办法，未同意的建议不算。"),
  ("execution_contract_reached", "执行契约", "用户同意执行本轮目标及记录安排；进入 M4 还须真实执行反馈。")),
 "module_4": (
  ("execution_reviewed", "执行与情境", "milestone_1：焦点PA实际开始、完成程度/时长明确，A实际PA且情绪改善，B未开始且明确障碍，C窗口已过排除A/B。未知不分类。"),
  ("abc_chain_completed", "ABC 核对", "milestone_2：当前事件ABC简洁总结获用户明确确认；纠正后旧确认失效，未知不编造。"),
  ("barriers_identified", "BA 教育与理解", "milestone_3（保留旧步骤键兼容）：ABC确认后完成情境化BA教育，用户基本理解且无未答核心疑问。不能提前给策略。"),
  ("coping_strategy_selected", "困难处理与应对", "milestone_4：只处理真实困难，继续时共同形成原目标策略；无需处理或明确调整可跳过具体策略，不改内容/时长。"),
  ("review_decision_made", "复盘决定", "用户明确继续、调整、更换或结束；普通日常活动反馈不等于决定结束复盘。继续同一目标沿用计划进入下一周期M4，调整才回M2新版本；后台核验对话中的明确用户决定后执行，不要求网页按钮。")),
}
MODULE_STEP_KEYS = {module: tuple(row[0] for row in rows) for module, rows in STEP_REGISTRY.items()}


def step_prompt_contract():
    keys = "\n".join(f"- {module} 可用步骤键：{', '.join(values)}。" for module,values in MODULE_STEP_KEYS.items())
    return "\n# 步骤注册表与判定规则（" + VERSION + "）\n" + keys + "\n输出 completed_steps 只使用裸 step key，不加 module_ 前缀。\n" + "\n".join(
        f"- {module}.{key}：{rule}" for module, rows in STEP_REGISTRY.items() for key, _, rule in rows)


def completed_steps_schema(module):
    return {"type":"array", "items":{"type":"string","enum":list(MODULE_STEP_KEYS[module])},
            "uniqueItems":True,"maxItems":len(MODULE_STEP_KEYS[module])}
