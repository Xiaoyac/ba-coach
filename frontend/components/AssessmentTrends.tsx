"use client";

import { useId, useState } from "react";
import type { AssessmentRecord } from "@/lib/assessment";

type Metric = "overall_mood" | "activity_level" | "completion_rate";

const METRICS: { key: Metric; label: string }[] = [
  { key: "overall_mood", label: "整体心情" },
  { key: "activity_level", label: "身体活动" },
  { key: "completion_rate", label: "完成程度" },
];
const DAY = 86_400_000;
const PLOT = { left: 27, right: 342, top: 16, bottom: 150 };

function calendarDay(value: string): number | null {
  // A local record date is a calendar date, independent of the viewer's zone.
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const time = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value
    ? time / DAY
    : null;
}

function rating(record: AssessmentRecord, metric: Metric): number | null {
  if (metric === "completion_rate" && record.completion_not_applicable) return null;
  const value = record[metric];
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 5
    ? value
    : null;
}

function ratingText(record: AssessmentRecord, metric: Metric): string {
  if (metric === "completion_rate" && record.completion_not_applicable) return "不适用";
  const value = rating(record, metric);
  return value === null ? "未填写" : `${value}/5`;
}

export default function AssessmentTrends({
  records,
  hasMore = false,
}: {
  records: AssessmentRecord[];
  hasMore?: boolean;
}) {
  const [metric, setMetric] = useState<Metric>("overall_mood");
  const id = useId();
  const label = METRICS.find((item) => item.key === metric)!.label;
  // Appending pages or merging a just-saved record must not count it twice.
  const latestById = new Map<number, AssessmentRecord>();
  for (const record of records) {
    if (record.status !== "completed" || record.scale_version !== 2 || calendarDay(record.local_date) === null) continue;
    const previous = latestById.get(record.id);
    if (!previous || (record.revision_no ?? 1) >= (previous.revision_no ?? 1)) latestById.set(record.id, record);
  }
  const recent = Array.from(latestById.values())
    .sort((a, b) => b.local_date.localeCompare(a.local_date) || b.id - a.id)
    .slice(0, 7)
    .reverse();
  const hasLegacy = records.some((record) => record.status === "completed" && record.scale_version !== 2);
  const firstDay = recent.length ? calendarDay(recent[0].local_date)! : 0;
  const lastDay = recent.length ? calendarDay(recent[recent.length - 1].local_date)! : 0;
  const points = recent.map((record) => {
    const day = calendarDay(record.local_date)!;
    const value = rating(record, metric);
    return {
      record,
      day,
      value,
      x: lastDay === firstDay ? (PLOT.left + PLOT.right) / 2 : PLOT.left + (day - firstDay) / (lastDay - firstDay) * (PLOT.right - PLOT.left),
      y: value === null ? null : PLOT.bottom - value / 5 * (PLOT.bottom - PLOT.top),
    };
  });
  const plotted = points.filter((point) => point.value !== null);
  // Break the line at missing ratings, N/A, and unrecorded calendar days.
  // A gap is never filled with zero or bridged by an inferred measurement.
  const path = points.map((point, index) => {
    if (point.y === null) return "";
    const previous = points[index - 1];
    const join = previous !== undefined && previous.y !== null && point.day - previous.day === 1;
    return `${join ? "L" : "M"}${point.x},${point.y}`;
  }).join(" ");

  return (
    <section aria-labelledby={`${id}-heading`} className="min-w-0 rounded-2xl border border-accent-edge bg-accent-wash p-4 sm:p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={`${id}-heading`} className="text-sm font-semibold text-ink">近期变化</h3>
        {recent.length > 0 && <span className="text-xs text-ink-muted">本图展示 {recent.length} 次记录 · 0–5 分</span>}
      </div>
      <p className="mt-2 text-xs leading-5 text-ink-muted">基于最近最多 7 次新版记录，未记录日期不代表 0。</p>
      {hasMore && <p className="mt-1 text-xs leading-5 text-ink-muted">从已加载的历史记录中选取；可在下方继续加载更早的记录。</p>}
      {hasLegacy && <p className="mt-1 text-xs leading-5 text-ink-muted">旧版总体评分为 0–10 分，不并入此图，仍可在历史记录中查看。</p>}

      {recent.length === 0 ? (
        <p className="mt-4 rounded-xl bg-sheet/70 px-4 py-5 text-sm leading-6 text-ink-muted">
          还没有可展示的新版记录。保存整体总结后，可以在这里回看自己填写的分数。
        </p>
      ) : (
        <>
          <div role="group" aria-label="选择趋势指标" className="mt-4 grid grid-cols-3 gap-1 rounded-xl bg-sheet/70 p-1">
            {METRICS.map((item) => (
              <button
                key={item.key}
                type="button"
                aria-pressed={metric === item.key}
                onClick={() => setMetric(item.key)}
                className={`min-h-11 rounded-lg px-1 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:text-sm ${metric === item.key ? "bg-accent text-on-accent" : "text-ink-muted hover:bg-raised"}`}
              >
                {item.label}
              </button>
            ))}
          </div>
          <p id={`${id}-description`} aria-live="polite" className="mt-3 text-xs leading-5 text-ink-muted">
            {plotted.length === 0
              ? `这些记录没有可绘制的${label}评分。`
              : plotted.length === 1
                ? `目前只有 1 次${label}评分，先保留这个点，之后可以对照更多记录。`
                : `${recent[0].local_date} 至 ${recent[recent.length - 1].local_date} 的${label}评分。`}
            {metric === "completion_rate" && " 不适用会留空，不作为 0 分。"}
          </p>

          {plotted.length > 0 && (
            <svg viewBox="0 0 360 180" role="img" aria-labelledby={`${id}-chart-title`} aria-describedby={`${id}-description`} className="mt-2 block h-auto max-h-56 w-full overflow-visible">
              <title id={`${id}-chart-title`}>{`${label}记录图；各日期分数可在下方展开查看`}</title>
              {[0, 1, 2, 3, 4, 5].map((score) => {
                const y = PLOT.bottom - score / 5 * (PLOT.bottom - PLOT.top);
                return (
                  <g key={score}>
                    <line x1={PLOT.left} x2={PLOT.right} y1={y} y2={y} stroke="currentColor" strokeOpacity="0.15" className="text-ink-muted" />
                    <text x="15" y={y + 4} textAnchor="end" fill="currentColor" fontSize="11" className="text-ink-muted">{score}</text>
                  </g>
                );
              })}
              <path d={path} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" className="text-accent-ink" />
              {points.map((point) => point.y === null ? null : (
                <circle key={point.record.id} cx={point.x} cy={point.y} r="3.5" fill="currentColor" className="text-accent-ink">
                  <title>{`${point.record.local_date} · ${label} ${point.value}/5`}</title>
                </circle>
              ))}
              {recent.length === 1 ? (
                <text x={(PLOT.left + PLOT.right) / 2} y="173" textAnchor="middle" fill="currentColor" fontSize="11" className="text-ink-muted">{recent[0].local_date.slice(5).replace("-", "/")}</text>
              ) : (
                <>
                  <text x={PLOT.left} y="173" textAnchor="start" fill="currentColor" fontSize="11" className="text-ink-muted">{recent[0].local_date.slice(5).replace("-", "/")}</text>
                  <text x={PLOT.right} y="173" textAnchor="end" fill="currentColor" fontSize="11" className="text-ink-muted">{recent[recent.length - 1].local_date.slice(5).replace("-", "/")}</text>
                </>
              )}
            </svg>
          )}
          <p className="mt-2 text-xs leading-5 text-ink-muted">横轴按实际日期排列，只连接相邻日期的已填评分；未记录或不适用处留空。</p>
          <details className="mt-3 border-t border-accent-edge pt-1">
            <summary className="cursor-pointer rounded-lg py-3 text-sm text-accent-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">查看各日期分数</summary>
            <div className="overflow-x-auto rounded-xl bg-sheet/70">
              <table className="w-full border-collapse text-left text-xs tabular-nums text-ink-muted">
                <caption className="sr-only">本图包含的最近最多七次新版记录；全部评分满分为五分，不适用不计为零分。</caption>
                <thead>
                  <tr className="border-b border-line">
                    <th scope="col" className="px-2 py-3 font-medium">日期</th>
                    {METRICS.map((item) => <th key={item.key} scope="col" className="px-2 py-3 font-medium">{item.label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {recent.map((record) => (
                    <tr key={record.id} className="border-b border-line last:border-0">
                      <th scope="row" className="whitespace-nowrap px-2 py-3 font-normal"><time dateTime={record.local_date}>{record.local_date}</time></th>
                      {METRICS.map((item) => <td key={item.key} className="px-2 py-3">{ratingText(record, item.key)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </section>
  );
}
