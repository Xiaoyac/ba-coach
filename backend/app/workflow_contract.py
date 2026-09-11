"""Versioned registry shared by routing, persistence, DMS and documentation."""
VERSION = "workflow-20260910-v1"
STEP_REGISTRY = {
 "module_1": (
  ("core_problem_example", "具体困扰事例", "已获得至少一个足以理解行为与情绪关联的具体事例；不是必须穷尽背景。"),
  ("depression_cycle_formulated", "循环理解", "结合该事例形成触发、行为及后果之间的理解，并与用户核对；不能只输出术语。"),
  ("ba_education_completed", "BA 教育", "已结合用户经历解释 BA 的活动与情绪关系，用户表达理解；不能仅凭教练说过。"),
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
  ("execution_reviewed", "执行复盘", "已有用户报告的实际执行或未执行情况，不从沉默推断。"),
  ("abc_chain_completed", "ABC 分析", "以实际事件核对前因、行为、后果，未知感受不编造。"),
  ("barriers_identified", "障碍识别", "讨论并确认本次障碍；确无障碍可以明确记录，不强行编造。"),
  ("coping_strategy_selected", "应对选择", "用户参与选择下一次可行策略或明确无需改变。"),
  ("review_decision_made", "复盘决定", "用户明确继续、调整、更换或结束的意向；当前旧流程仅支持既有循环转换。")),
}
MODULE_STEP_KEYS = {module: tuple(row[0] for row in rows) for module, rows in STEP_REGISTRY.items()}


def step_prompt_contract():
    keys = "\n".join(f"- {module} 可用步骤键：{', '.join(values)}。" for module,values in MODULE_STEP_KEYS.items())
    return "\n# 步骤注册表与判定规则（" + VERSION + "）\n" + keys + "\n输出 completed_steps 只使用裸 step key，不加 module_ 前缀。\n" + "\n".join(
        f"- {module}.{key}：{rule}" for module, rows in STEP_REGISTRY.items() for key, _, rule in rows)


def completed_steps_schema(module):
    return {"type":"array", "items":{"type":"string","enum":list(MODULE_STEP_KEYS[module])},
            "uniqueItems":True,"maxItems":len(MODULE_STEP_KEYS[module])}
