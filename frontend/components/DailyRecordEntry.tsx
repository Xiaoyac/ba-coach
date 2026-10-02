"use client";

import { useEffect, useState } from "react";
import { NotebookMark } from "@/components/icons";
import { fetchAssessmentByDate } from "@/lib/assessment";

type RecordStatus = "loading" | "error" | "none" | "completed";

function localToday(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

const STATUS_LABELS: Record<RecordStatus, string> = {
  loading: "正在查看今日记录…",
  error: "今日状态暂时无法读取",
  none: "今天暂无记录",
  completed: "今天已记录",
};

export default function DailyRecordEntry({
  onOpen,
  onHistory,
  refreshKey = 0,
}: {
  onOpen: () => void;
  onHistory: () => void;
  refreshKey?: number;
}) {
  const [status, setStatus] = useState<RecordStatus>("loading");
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    let active = true;
    let controller: AbortController | undefined;
    let requestedDate = "";

    async function refresh() {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      const date = localToday();
      requestedDate = date;
      setStatus("loading");

      try {
        const record = await fetchAssessmentByDate(date, request.signal);
        if (!active || request.signal.aborted) return;
        // A response for yesterday must not become today's completion state.
        if (date !== localToday()) {
          void refresh();
          return;
        }
        setStatus(record?.status === "completed" ? "completed" : "none");
      } catch {
        if (!active || request.signal.aborted) return;
        if (date !== localToday()) {
          void refresh();
          return;
        }
        setStatus("error");
      }
    }

    function onFocus() {
      void refresh();
    }

    function onVisibilityChange() {
      if (document.visibilityState === "visible") void refresh();
    }

    void refresh();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibilityChange);
    const rolloverCheck = window.setInterval(() => {
      if (requestedDate !== localToday()) void refresh();
    }, 30_000);

    return () => {
      active = false;
      controller?.abort();
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.clearInterval(rolloverCheck);
    };
  }, [refreshKey, retryKey]);

  return (
    <section
      aria-label="每日记录快捷入口"
      className="mb-3 rounded-2xl border border-accent-edge bg-accent-wash p-3 sm:px-4"
    >
      <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <NotebookMark className="h-4 w-4 shrink-0 text-accent-ink" />
            <h2 className="text-sm font-semibold text-ink">每日记录</h2>
            <span role="status" aria-live="polite" className="text-xs text-ink-muted">
              {STATUS_LABELS[status]}
            </span>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-ink-muted">
            每天记下整天的感受，有没有运动都可以。3 项整体评分，活动选填；保存后可回看历史与趋势。
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onOpen}
            aria-haspopup="dialog"
            className="min-h-11 flex-1 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-on-accent transition-colors hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:flex-none"
          >
            {status === "completed" ? "修改今天" : "记录今天"}
          </button>
          <button
            type="button"
            onClick={onHistory}
            aria-haspopup="dialog"
            className="min-h-11 flex-1 rounded-xl border border-accent-edge px-3 py-2 text-sm font-medium text-accent-ink transition-colors hover:bg-sheet focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:flex-none"
          >
            历史与趋势
          </button>
          {status === "error" && (
            <button
              type="button"
              onClick={() => setRetryKey((key) => key + 1)}
              aria-label="重试读取今日记录状态"
              className="min-h-11 min-w-11 rounded-xl px-2 py-2 text-xs text-accent-ink underline underline-offset-4 transition-colors hover:bg-sheet focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              重试
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
