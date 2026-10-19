import type { AssessmentRecord } from "@/lib/assessment";

/**
 * The chat message posted after a daily record is saved, so the coach reads
 * the record through the conversation (like a submitted goal card).
 */
const ACTIVITY_SCORES = [
  ["achievement", "成就感"], ["connection", "联结感"], ["enjoyment", "享受感"], ["importance", "重视感"],
] as const;

export function dailyRecordMessage(record: AssessmentRecord, updated: boolean): string {
  const [, month, day] = record.local_date.split("-").map(Number);
  const maximum = record.scale_version === 2 ? 5 : 10;
  const lines = [`我${updated ? "更新" : "填写"}了${month}月${day}日的每日记录：`];
  if (record.activities.length) {
    lines.push("活动：");
    record.activities.forEach((a, index) => {
      const parts = [[a.time_slot, a.activity].filter(Boolean).join(" ")];
      if (a.emotion !== null) parts.push(`情绪 ${a.emotion}/5`);
      const scores = ACTIVITY_SCORES.filter(([key]) => a[key] !== null).map(([key, label]) => `${label} ${a[key]}/5`);
      if (scores.length) parts.push(scores.join("、"));
      if (a.note) parts.push(`备注：${a.note}`);
      lines.push(`${index + 1}. ${parts.join("｜")}`);
    });
  }
  const summary = [
    record.completion_not_applicable ? "想做的事情完成程度 不适用"
      : record.completion_rate !== null ? `想做的事情完成程度 ${record.completion_rate}/${maximum}` : null,
    record.activity_level !== null ? `身体活动程度 ${record.activity_level}/${maximum}` : null,
    record.overall_mood !== null ? `整体心情 ${record.overall_mood}/${maximum}` : null,
    record.social_connection !== null ? `社会联结 ${record.social_connection}/${maximum}` : null,
    record.approach_vs_avoidance !== null ? `面对与回避 ${record.approach_vs_avoidance}/${maximum}` : null,
  ].filter(Boolean);
  if (summary.length) lines.push(`这一天：${summary.join("；")}`);
  if (record.reflection_note) lines.push(`感想：${record.reflection_note}`);
  return lines.join("\n");
}
