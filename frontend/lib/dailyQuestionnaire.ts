/**
 * 每日测量 wording, from the study's "Poc每日测量" form.
 *
 * The study team may still revise this text, so every question, option and
 * instruction shown in the 每日测量 tab lives here as data. Field keys match
 * the API (`@/lib/dailyMeasurement`) and must not change without a backend
 * migration; wording can change freely.
 */

import type { MeasurementAnswers } from "@/lib/dailyMeasurement";

export type MeasureKey = keyof MeasurementAnswers;
export type MeasureDraft = { [K in MeasureKey]: number | null };

export const MEASURE_INTRO = [
  "您好！欢迎使用本问卷进行每日活动和情绪记录。您可以在每天的任意时间段打开本问卷，对当天的活动与情绪进行记录，在确定已完成当天记录后，于当日结束前完成提交。",
  "本问卷所收集的全部数据仅用于学术研究，所有信息将严格匿名处理。请您根据真实感受放心作答。您的认真参与对本研究具有重要意义，衷心感谢您的支持与配合！",
  "如果您有任何疑问，可以联系：hualam_1220（微信号）",
];

/** Which date to pick when filling in late at night. */
export const MEASURE_DATE_GUIDE = [
  "如 7月23日 22:00 填写完当天活动并提交，则选择 7月23日；",
  "如 7月24日凌晨 2 点填写 7月23日的活动并提交，则选择 7月23日。",
];

/** Asked first, about the night before the record date. */
export const MEASURE_SLEEP = {
  hours: { question: "昨晚，实际睡眠大概多少小时？", note: "可填一位小数，如 6.5。" },
  quality: { question: "总的来说，您认为昨晚的睡眠质量", options: ["很好", "较好", "较差", "很差"] },
};
export const MEASURE_MAX_SLEEP_HOURS = 24;

export const MEASURE_DAY_INSTRUCTIONS = [
  "请回顾您所选择的记录天数，按照这一天的真实情况回答。通常建议晚上填写当天情况；如果是补填，请回答所选日期当天的情况。",
  "没有标准答案，也不需要为了研究而显得“状态更好”或“活动更多”。请按真实情况填写。",
];

/** Q7–Q9: five labelled options, stored 1–5 from the first to the last. */
export const MEASURE_OVERALL: { key: "completion" | "activity_level" | "overall_mood"; short: string; question: string; options: string[] }[] = [
  { key: "completion", short: "完成程度", question: "回顾这一天，您在多大程度上完成了自己计划或想做的事情？",
    options: ["完全没有完成", "大部分没有完成", "一般", "大部分完成", "全部完成"] },
  { key: "activity_level", short: "身体活动", question: "回顾这一天，您的身体活动总体处于什么程度？",
    options: ["几乎没有身体活动", "较少身体活动", "一般", "较多身体活动", "身体活动非常多"] },
  { key: "overall_mood", short: "整体心情", question: "回顾这一天，您的整体心情如何？",
    options: ["非常糟糕", "比较糟糕", "一般", "比较愉悦", "非常愉悦"] },
];

export type MeasureEmotionKey = "energetic" | "peaceful" | "secure" | "low_mood" | "worthless" | "irritable" | "tired" | "stressed" | "lonely";

/** Q10: 1–5 agreement under one shared legend. */
export const MEASURE_EMOTION_QUESTION = "请判断下面每种感受与您这一天整体体验的符合程度";
export const MEASURE_AGREEMENT = ["完全不符合", "有点不符合", "不确定", "有点符合", "非常符合"];
export const MEASURE_EMOTIONS: { key: MeasureEmotionKey; label: string }[] = [
  { key: "energetic", label: "有活力的" },
  { key: "peaceful", label: "安宁的" },
  { key: "secure", label: "有安全感的" },
  { key: "low_mood", label: "低落或沮丧的" },
  { key: "worthless", label: "感觉自己没有价值" },
  { key: "irritable", label: "烦躁的" },
  { key: "tired", label: "疲倦的" },
  { key: "stressed", label: "感觉有压力的" },
  { key: "lonely", label: "孤独的" },
];

/** Q11: occurrences and total minutes per intensity, in the form's order. */
export const MEASURE_ACTIVITY_INSTRUCTIONS = [
  "请回顾所选日期全天的身体活动，包括工作、通勤、家务、日常生活以及休闲锻炼中的活动。",
  "请根据活动时的实际费力程度，将身体活动归入低、中、高强度，分别填写：1. 当天一共发生了多少次；2. 当天累计进行了多少分钟。",
];
export const MEASURE_ACTIVITY_EXAMPLE = "例：中等强度活动中，我快走 2 次，扫地 1 次，游泳 1 次，今日共 4 次，这些活动累计进行了 50 分钟。";
export const MEASURE_ACTIVITY_COLUMNS = { count: "今天总共发生了几次？", minutes: "累计时长（以分钟为单位）" };

type ActivityKey = Exclude<MeasureKey, MeasureEmotionKey | "sleep_hours" | "sleep_quality" | "completion" | "activity_level" | "overall_mood">;
export const MEASURE_INTENSITIES: { name: string; description: string; examples: string; count: ActivityKey; minutes: ActivityKey }[] = [
  { name: "剧烈运动", description: "心跳显著加快", examples: "跑步、足球、篮球、羽毛球比赛、爬山、高强度游泳、搬运重物、长距离高强度骑行",
    count: "vigorous_count", minutes: "vigorous_minutes" },
  { name: "中等强度运动", description: "不至于精疲力尽", examples: "快走、慢速骑行、轻度游泳、流行舞、浇花、擦窗户、扫地",
    count: "moderate_count", minutes: "moderate_minutes" },
  { name: "轻度运动", description: "几乎不费力", examples: "散步、瑜伽、射箭、钓鱼、保龄球",
    count: "light_count", minutes: "light_minutes" },
];

// Per intensity: at most 20 occurrences and 480 minutes a day.
export const MEASURE_MAX_COUNT = 20;
export const MEASURE_MAX_MINUTES = 480;
export const MEASURE_DAY_MINUTES = 1440;

export const MEASURE_KEYS: MeasureKey[] = [
  "sleep_hours", "sleep_quality",
  ...MEASURE_OVERALL.map(q => q.key),
  ...MEASURE_EMOTIONS.map(e => e.key),
  ...MEASURE_INTENSITIES.flatMap(i => [i.count, i.minutes]),
];

export const emptyMeasure = (): MeasureDraft => Object.fromEntries(MEASURE_KEYS.map(k => [k, null])) as MeasureDraft;

/** First problem with the draft, in form order, or null when it can be sent. */
export function measureProblem(draft: MeasureDraft): string | null {
  if (draft.sleep_hours === null) return "请填写昨晚的睡眠小时数";
  if (draft.sleep_hours > MEASURE_MAX_SLEEP_HOURS) return `睡眠小时数最多 ${MEASURE_MAX_SLEEP_HOURS}`;
  if (draft.sleep_quality === null) return "请选择昨晚的睡眠质量";
  const overall = MEASURE_OVERALL.find(q => draft[q.key] === null);
  if (overall) return `请回答“${overall.short}”这一题`;
  if (MEASURE_EMOTIONS.some(e => draft[e.key] === null)) return "请完成 9 种感受的符合程度";
  for (const level of MEASURE_INTENSITIES) {
    const count = draft[level.count], minutes = draft[level.minutes];
    if (count === null || minutes === null) return "请填写三种强度身体活动的次数和累计时长，没有就填 0";
    if (count > MEASURE_MAX_COUNT) return `${level.name}的次数最多 ${MEASURE_MAX_COUNT} 次`;
    if (minutes > MEASURE_MAX_MINUTES) return `${level.name}的累计时长最多 ${MEASURE_MAX_MINUTES} 分钟`;
    if (minutes > 0 && count === 0) return `${level.name}填写了累计时长，次数至少为 1`;
  }
  const total = MEASURE_INTENSITIES.reduce((sum, level) => sum + (draft[level.minutes] ?? 0), 0);
  if (total > MEASURE_DAY_MINUTES) return "三种强度的累计时长合计不能超过 1440 分钟（一天）";
  return null;
}
