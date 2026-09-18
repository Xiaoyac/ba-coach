"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDownMark, HistoryMark } from "@/components/icons";
import type { AssessmentRecord, StoredActivityLog } from "@/lib/assessment";

const SUMMARY_LABELS: {
  key: keyof Pick<
    AssessmentRecord,
    | "completion_rate"
    | "activity_level"
    | "social_connection"
    | "approach_vs_avoidance"
    | "overall_mood"
  >;
  label: string;
}[] = [
  { key: "completion_rate", label: "完成度" },
  { key: "activity_level", label: "活动量" },
  { key: "social_connection", label: "联结" },
  { key: "approach_vs_avoidance", label: "面对" },
  { key: "overall_mood", label: "心情" },
];

const ACTIVITY_LABELS: {
  key: keyof Pick<
    StoredActivityLog,
    "emotion" | "achievement" | "connection" | "enjoyment" | "importance"
  >;
  label: string;
}[] = [
  { key: "emotion", label: "情绪" },
  { key: "achievement", label: "成就" },
  { key: "connection", label: "联结" },
  { key: "enjoyment", label: "愉悦" },
  { key: "importance", label: "重要" },
];

function formatLocalDate(value: string): string {
  // Noon UTC avoids a browser in a negative offset turning YYYY-MM-DD into
  // the previous day. `local_date` is a calendar date, not an instant.
  const date = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "UTC",
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(date);
}

export default function AssessmentHistory({
  records,
  loading,
  loadingMore,
  error,
  hasMore,
  onLoadMore,
  onRetry,
}: {
  records: AssessmentRecord[];
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  hasMore: boolean;
  onLoadMore: () => void;
  onRetry: () => void;
}) {
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const choseInitial = useRef(false);

  useEffect(() => {
    if (!choseInitial.current && records.length > 0) {
      choseInitial.current = true;
      setExpandedId(records[0].id);
    }
  }, [records]);

  if (loading && records.length === 0) return <HistorySkeleton />;

  if (!loading && records.length === 0 && !error) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center px-6 text-center">
        <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-accent-edge bg-accent-wash text-accent-ink">
          <HistoryMark className="h-5 w-5" />
        </span>
        <h3 className="mt-4 text-[0.95rem] font-medium text-ink">还没有历史记录</h3>
        <p className="mt-1.5 max-w-xs text-[0.8rem] leading-relaxed text-ink-faint">
          完成今天的行为记录后，它会安静地保存在这里，方便以后回看。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {error && (
        <div className="flex items-center justify-between gap-3 rounded-2xl border border-alert-edge bg-alert-wash px-4 py-3">
          <p className="text-[0.82rem] leading-relaxed text-alert-ink">{error}</p>
          <button
            type="button"
            onClick={onRetry}
            className="shrink-0 rounded-full border border-alert-edge px-3 py-1.5 text-[0.72rem] text-alert-ink transition-colors hover:bg-raised"
          >
            重新加载
          </button>
        </div>
      )}

      {records.map((record) => {
        const expanded = expandedId === record.id;
        const modern = record.scale_version === 2;
        const maximum = modern ? 5 : 10;
        const labels = modern ? SUMMARY_LABELS.filter(s => ["completion_rate", "activity_level", "overall_mood"].includes(s.key)) : SUMMARY_LABELS;
        return (
          <article
            key={record.id}
            className={`overflow-clip rounded-[22px] border bg-raised depth-bubble transition-colors duration-300 ${
              expanded ? "border-accent-edge" : "border-line"
            }`}
          >
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => setExpandedId(expanded ? null : record.id)}
              className="flex w-full items-center gap-3 px-4 py-4 text-left sm:px-5"
            >
              <span className="flex h-10 w-10 shrink-0 flex-col items-center justify-center rounded-2xl border border-accent-edge bg-accent-wash text-accent-ink">
                <span className="text-[0.68rem] leading-none opacity-70">
                  {record.local_date.slice(5, 7)}月
                </span>
                <span className="mt-0.5 text-sm font-medium leading-none tabular-nums">
                  {record.local_date.slice(8, 10)}
                </span>
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[0.9rem] font-medium text-ink">
                  {formatLocalDate(record.local_date)}
                </span>
                <span className="mt-1 block text-[0.75rem] text-ink-faint">
                  {record.activities.length} 项活动 · 整体心情 {record.overall_mood ?? "—"}/{maximum}{!modern && " · 旧版量表"}
                </span>
              </span>
              <ChevronDownMark
                className={`h-4 w-4 shrink-0 text-ink-faint transition-transform duration-300 ${
                  expanded ? "rotate-180" : ""
                }`}
              />
            </button>

            {expanded && (
              <div className="border-t border-line px-4 pb-5 pt-4 sm:px-5">
                <div className={`grid ${modern ? "grid-cols-3" : "grid-cols-5"} gap-1.5`}>
                  {labels.map(({ key, label }) => (
                    <div
                      key={key}
                      className="rounded-xl border border-line bg-canvas/35 px-1.5 py-2 text-center"
                    >
                      <p className="text-[0.62rem] text-ink-faint">{label}</p>
                      <p className="mt-0.5 text-[0.82rem] font-medium tabular-nums text-ink">
                        {key === "completion_rate" && record.completion_not_applicable ? "不适用" : <>{record[key] ?? "—"}
                        <span className="ml-0.5 text-[0.58rem] font-normal text-ink-faint">/{maximum}</span></>}
                      </p>
                    </div>
                  ))}
                </div>

                <div className="mt-4 space-y-2.5">
                  {record.activities.map((activity) => (
                    <ActivityHistoryCard key={`${record.id}-${activity.position}`} activity={activity} />
                  ))}
                </div>

                {record.reflection_note && (
                  <div className="mt-4 rounded-2xl border border-accent-edge bg-accent-wash px-4 py-3">
                    <p className="text-[0.65rem] tracking-wide text-accent-ink/70">那天写下的话</p>
                    <p className="mt-1 text-[0.82rem] leading-relaxed text-ink-muted">
                      {record.reflection_note}
                    </p>
                  </div>
                )}
              </div>
            )}
          </article>
        );
      })}

      {hasMore && (
        <button
          type="button"
          onClick={onLoadMore}
          disabled={loadingMore}
          className="flex w-full items-center justify-center gap-2 rounded-2xl border border-line bg-raised px-4 py-3 text-[0.82rem] text-ink-muted transition-colors duration-300 hover:border-accent-edge hover:text-accent-ink disabled:opacity-50"
        >
          {loadingMore && (
            <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line-strong border-t-accent motion-reduce:animate-none" />
          )}
          {loadingMore ? "正在载入…" : "查看更多记录"}
        </button>
      )}
    </div>
  );
}

function ActivityHistoryCard({ activity }: { activity: StoredActivityLog }) {
  return (
    <section className="rounded-2xl border border-line bg-canvas/25 px-3.5 py-3">
      <div className="flex items-start gap-3">
        <span className="shrink-0 rounded-full border border-accent-edge bg-accent-wash px-2.5 py-1 text-[0.68rem] tabular-nums text-accent-ink">
          {activity.time_slot}
        </span>
        <div className="min-w-0 flex-1">
          <p className="break-words text-[0.86rem] leading-relaxed text-ink">{activity.activity}</p>
          {activity.note && (
            <p className="mt-1 break-words text-[0.74rem] leading-relaxed text-ink-faint">
              {activity.note}
            </p>
          )}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1.5 border-t border-line pt-2.5">
        {ACTIVITY_LABELS.map(({ key, label }) => (
          <span key={key} className="text-[0.68rem] text-ink-faint">
            {label} <strong className="font-medium tabular-nums text-ink-muted">{activity[key] == null ? "未填写" : `${activity[key]}/5`}</strong>
          </span>
        ))}
      </div>
    </section>
  );
}

function HistorySkeleton() {
  return (
    <div className="space-y-3" aria-label="正在加载历史记录" role="status">
      {[0, 1, 2].map((item) => (
        <div key={item} className="rounded-[22px] border border-line bg-raised px-4 py-4 sm:px-5">
          <div className="flex animate-pulse items-center gap-3 motion-reduce:animate-none">
            <span className="h-10 w-10 rounded-2xl bg-accent-wash" />
            <span className="flex-1 space-y-2">
              <span className="block h-3 w-28 rounded-full bg-line-strong" />
              <span className="block h-2.5 w-20 rounded-full bg-line" />
            </span>
            <span className="h-4 w-4 rounded-full bg-line" />
          </div>
        </div>
      ))}
    </div>
  );
}
