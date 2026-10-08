"""Versioned registry shared by routing, persistence, DMS and documentation."""
VERSION = "workflow-20261001-m4-close-m2-choice"
STEP_REGISTRY = {
 "module_1": (
  ("core_problem_example", "充分经历或一般解释", "milestone_1：已有真实应对及实际结果即可，未知情境/体验保留，不要求四字段齐全。用户只问原理、无具体困扰/可用素材或明确不愿披露时可采用一般解释，须有实际对话依据。"),
  ("depression_cycle_formulated", "行为与状态关系", "milestone_2：已基于真实应对和结果给出足以支持教育的关系解释，不强制整套总结正式认可、缓解方法或负性循环；明确反对须先修正。一般解释须说明个性化理解有限，不另索取限制确认。"),
  ("ba_education_completed", "BA 教育与理解", "milestone_3 的理解门：三个核心 BA 含义已得到必要解释，用户基本理解且无未解决核心疑问；一般解释也须有真实理解依据，不能由教练讲完推定。"),
  ("goal_setting_consent", "目标设定意愿", "用户明确愿意开始目标设定；沉默、模糊附和或对其他问题说继续不算。")),
 "module_2": (
  ("pa_concept_understood", "PA 理解", "用户理解 PA 是具体行动任务，与 BA 方法区分。"),
  ("values_or_intention_explored", "价值与意向", "已探索本轮活动的个人意义或行动意向，不强制预设价值。"),
  ("activity_selected", "活动选择", "用户选择具体活动，尚未选择的备选方案不算。"),
  ("pa_card_completed", "目标卡完成", "本轮活动和仍可执行的时间明确，有真实来源的难度评分不高于5，实际障碍有应对；用户明确没有障碍时允许有来源的无需应对记录。地点、时长、频率按实际需要保留，不强制补齐；3天内仅建议，明确选择更晚时间不阻断。须经当前版本核验、展示和用户确认。")),
 "module_3": (
  ("recording_explained", "记录说明", "已说明记录内容、用途和操作方式。"),
  ("recording_plan_agreed", "记录计划", "用户同意实际可行的记录办法，未同意的建议不算。"),
  ("execution_contract_reached", "执行契约", "用户同意执行本轮目标及记录安排；确认后留在 M3 的 waiting_execution 等待态，不因确认自动转入 M4。只有真实执行反馈才支持 M4 的 execution_reviewed。")),
 "module_4": (
  ("execution_reviewed", "执行与情境", "milestone_1：焦点PA实际开始及完成程度明确，实际时长未知保留；A实际PA后有用户确认的情绪/身体感觉改善、成就/满足、自我积极认识或促进后续行动意愿，B未开始且明确障碍，C窗口已过排除A/B。执行、情绪和其他积极反馈分别记录；情绪未改善不代表其他反馈无益，未知不分类，不要求逐类追问。"),
  ("abc_chain_completed", "ABC 核对", "milestone_2：当前事件ABC简洁总结获用户明确确认；纠正后旧确认失效，未知不编造。"),
  ("barriers_identified", "BA 教育与理解", "milestone_3（保留旧步骤键兼容）：ABC确认后完成情境化BA教育，用户基本理解且无未答核心疑问。不能提前给策略。"),
  ("coping_strategy_selected", "困难处理与应对", "milestone_4：只处理真实困难，继续时共同形成原目标策略；无需处理或明确调整可跳过具体策略，不改内容/时长。"),
  ("review_decision_made", "本轮复盘收尾", "兼容旧步骤键：本轮执行结果已确定，必要复盘完成并总结后结束本轮，进入M2讨论保留或替换目标。执行前暂时犹豫不结束本轮，最终明确不执行或窗口结束才收尾；不要求在M4决定下一目标，不自动跳M3。")),
}
MODULE_STEP_KEYS = {module: tuple(row[0] for row in rows) for module, rows in STEP_REGISTRY.items()}


def step_prompt_contract():
    keys = "\n".join(f"- {module} 可用步骤键：{', '.join(values)}。" for module,values in MODULE_STEP_KEYS.items())
    return "\n# 步骤注册表与判定规则（" + VERSION + "）\n" + keys + "\n输出 completed_steps 只使用裸 step key，不加 module_ 前缀。\n" + "\n".join(
        f"- {module}.{key}：{rule}" for module, rows in STEP_REGISTRY.items() for key, _, rule in rows)


def completed_steps_schema(module):
    return {"type":"array", "items":{"type":"string","enum":list(MODULE_STEP_KEYS[module])},
            "uniqueItems":True,"maxItems":len(MODULE_STEP_KEYS[module])}
