import type { GoalCardFields } from "./goal-card";

/** Copy reference information only. Old dates, difficulty and consent are not transferable. */
export function goalReuseDraft(fields: GoalCardFields): string {
  const reference = [fields.activity_content && `活动：${fields.activity_content}`,
    fields.location && `参考地点：${fields.location}`,
    fields.duration_minutes != null && `参考时长：${fields.duration_minutes} 分钟`,
    fields.long_term_direction && `在意的方向：${fields.long_term_direction}`].filter(Boolean).join("\n");
  return `我想参考以前的安排，讨论一份新的活动计划：\n${reference}\n这次的日期、频率和难度需要重新确定。请保留原有计划和执行历史，新计划核对后再由我确认。`;
}
