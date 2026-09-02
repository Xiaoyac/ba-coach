"use client";

import { useEffect, useState } from "react";
import { CameraMark, CheckMark, CloseMark, ReportMark } from "@/components/icons";
import {
  fetchAdminIssueReports,
  fetchIssueScreenshot,
  updateIssueReportStatus,
  type AdminIssueReportItem,
} from "@/lib/issueReports";

function formatDate(value: string): string {
  return new Date(value).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function AdminIssueReportModal({ onClose }: { onClose: () => void }) {
  const [filter, setFilter] = useState<"open" | "resolved" | "all">("open");
  const [reports, setReports] = useState<AdminIssueReportItem[]>([]);
  const [selected, setSelected] = useState<AdminIssueReportItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [screenshotUrl, setScreenshotUrl] = useState<string | null>(null);
  const [screenshotLoading, setScreenshotLoading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void fetchAdminIssueReports(filter, controller.signal)
      .then((items) => {
        setReports(items);
        setSelected((current) =>
          current ? items.find((item) => item.id === current.id) ?? null : null,
        );
      })
      .catch((err) => {
        if ((err as { name?: string })?.name !== "AbortError") {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [filter]);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    setScreenshotUrl(null);
    if (!selected?.has_screenshot) return;
    setScreenshotLoading(true);
    void fetchIssueScreenshot(selected.id)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setScreenshotUrl(objectUrl);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setScreenshotLoading(false);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [selected?.id, selected?.has_screenshot]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (selected) setSelected(null);
      else onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, selected]);

  async function toggleStatus() {
    if (!selected || updating) return;
    setUpdating(true);
    setError(null);
    try {
      const updated = await updateIssueReportStatus(
        selected.id,
        selected.status === "open" ? "resolved" : "open",
      );
      setSelected(updated);
      setReports((prev) => {
        if (filter !== "all" && updated.status !== filter) {
          return prev.filter((item) => item.id !== updated.id);
        }
        return prev.map((item) => (item.id === updated.id ? updated : item));
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUpdating(false);
    }
  }

  return (
    <div className="zen-overlay-enter fixed inset-0 z-[70] flex items-center justify-center p-3 sm:p-5">
      <button
        type="button"
        aria-label="关闭问题反馈管理"
        onClick={onClose}
        className="absolute inset-0 bg-canvas/75 backdrop-blur-md"
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="issue-manager-title"
        className="relative flex h-[min(800px,calc(100dvh-1.5rem))] min-h-0 w-full max-w-5xl flex-col overflow-hidden rounded-[28px] border border-line bg-panel depth-panel backdrop-blur-2xl sm:h-[min(800px,calc(100dvh-2.5rem))]"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-line px-5 py-4 sm:px-6">
          <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-accent-edge bg-accent-wash text-accent-ink">
            <ReportMark className="h-4.5 w-4.5" />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 id="issue-manager-title" className="text-base font-medium text-ink">
                管理员 · 问题反馈
              </h2>
              <span className="rounded-full bg-accent-wash px-2 py-0.5 text-[0.62rem] tracking-[0.12em] text-accent-ink">
                ADMIN
              </span>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-ink-faint">
              查看用户说明、自动截图与提交时的页面环境。截图可能包含对话内容，请仅用于排查。
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="ml-auto grid h-9 w-9 shrink-0 place-items-center rounded-full text-ink-muted transition-colors duration-300 hover:bg-raised hover:text-ink"
          >
            <CloseMark className="h-4 w-4" />
          </button>
        </header>

        <div className="flex shrink-0 gap-1.5 border-b border-line px-4 py-3 sm:px-6">
          {(["open", "resolved", "all"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setFilter(value)}
              className={`rounded-full px-3 py-1.5 text-xs transition-colors duration-300 ${
                filter === value
                  ? "border border-accent-edge bg-accent-wash text-accent-ink"
                  : "border border-transparent text-ink-faint hover:bg-raised hover:text-ink"
              }`}
            >
              {value === "open" ? "待处理" : value === "resolved" ? "已处理" : "全部"}
            </button>
          ))}
        </div>

        {error && (
          <p role="alert" className="mx-4 mt-3 shrink-0 rounded-xl border border-alert-edge bg-alert-wash px-3 py-2 text-xs text-alert-ink sm:mx-6">
            {error}
          </p>
        )}

        <div className="grid min-h-0 flex-1 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.25fr)]">
          <div className={`zen-scroll min-h-0 overflow-y-auto p-4 sm:p-5 ${selected ? "hidden md:block" : "block"}`}>
            {loading ? (
              <div className="grid h-full min-h-32 place-items-center" aria-busy="true">
                <span className="h-7 w-7 animate-spin rounded-full border-2 border-line-strong border-t-accent" />
              </div>
            ) : reports.length === 0 ? (
              <div className="grid h-full min-h-32 place-items-center text-sm text-ink-faint">
                这里暂时没有反馈
              </div>
            ) : (
              <div className="space-y-2.5">
                {reports.map((report) => (
                  <button
                    key={report.id}
                    type="button"
                    onClick={() => setSelected(report)}
                    className={`w-full rounded-2xl border p-3.5 text-left transition-colors duration-300 ${
                      selected?.id === report.id
                        ? "border-accent-edge bg-accent-wash"
                        : "border-line bg-raised hover:border-accent-edge"
                    }`}
                  >
                    <div className="flex items-center gap-2 text-[0.68rem] text-ink-faint">
                      <span>#{report.id}</span>
                      <span>{formatDate(report.created_at)}</span>
                      {report.has_screenshot && <CameraMark className="ml-auto h-3.5 w-3.5" />}
                    </div>
                    <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-ink">
                      {report.description}
                    </p>
                    <p className="mt-2 truncate text-[0.68rem] text-ink-muted">
                      {report.display_name ?? report.username}
                    </p>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className={`zen-scroll min-h-0 overflow-y-auto border-line p-4 sm:p-5 md:border-l ${selected ? "block" : "hidden md:block"}`}>
            {!selected ? (
              <div className="grid h-full min-h-40 place-items-center text-sm text-ink-faint">
                选择一条反馈查看详情
              </div>
            ) : (
              <article>
                <div className="flex items-start gap-3">
                  <button
                    type="button"
                    onClick={() => setSelected(null)}
                    className="rounded-full px-2.5 py-1 text-xs text-ink-muted hover:bg-raised md:hidden"
                  >
                    返回
                  </button>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs text-ink-faint">反馈 #{selected.id}</span>
                      <span className={`rounded-full px-2 py-0.5 text-[0.62rem] ${selected.status === "open" ? "bg-alert-wash text-alert-ink" : "bg-accent-wash text-accent-ink"}`}>
                        {selected.status === "open" ? "待处理" : "已处理"}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-ink-muted">
                      {selected.display_name ?? selected.username} · 登录账号 {selected.username}
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={updating}
                    onClick={() => void toggleStatus()}
                    className="shrink-0 rounded-xl border border-accent-edge bg-accent-wash px-3 py-2 text-xs text-accent-ink transition-colors hover:bg-raised disabled:cursor-wait disabled:opacity-50"
                  >
                    {updating ? "更新中…" : selected.status === "open" ? "标记已处理" : "重新打开"}
                  </button>
                </div>

                <div className="mt-4 rounded-2xl border border-line bg-raised p-4">
                  <p className="whitespace-pre-wrap text-sm leading-[1.8] text-ink">{selected.description}</p>
                </div>

                <div className="mt-4 overflow-hidden rounded-2xl border border-line bg-canvas/45">
                  <div className="flex items-center gap-2 border-b border-line px-3.5 py-2.5 text-xs text-ink-muted">
                    <CameraMark className="h-4 w-4" />
                    提交时截图
                  </div>
                  {screenshotLoading ? (
                    <div className="grid h-48 place-items-center"><span className="h-6 w-6 animate-spin rounded-full border-2 border-line-strong border-t-accent" /></div>
                  ) : screenshotUrl ? (
                    <a href={screenshotUrl} target="_blank" rel="noreferrer" title="在新标签页查看原图">
                      <img src={screenshotUrl} alt={`问题反馈 ${selected.id} 的页面截图`} className="max-h-80 w-full object-contain" />
                    </a>
                  ) : (
                    <div className="grid h-32 place-items-center text-xs text-ink-faint">用户没有附加截图</div>
                  )}
                </div>

                <dl className="mt-4 grid gap-2 rounded-2xl border border-line bg-raised p-4 text-[0.72rem] leading-relaxed sm:grid-cols-2">
                  <div><dt className="text-ink-faint">提交时间</dt><dd className="mt-0.5 text-ink">{formatDate(selected.created_at)}</dd></div>
                  <div><dt className="text-ink-faint">视窗</dt><dd className="mt-0.5 text-ink">{selected.viewport_width ?? "?"} × {selected.viewport_height ?? "?"}</dd></div>
                  <div className="sm:col-span-2"><dt className="text-ink-faint">会话 ID</dt><dd className="mt-0.5 break-all font-mono text-ink">{selected.session_id ?? "未提供"}</dd></div>
                  <div className="sm:col-span-2"><dt className="text-ink-faint">页面</dt><dd className="mt-0.5 break-all text-ink">{selected.page_url || "未提供"}</dd></div>
                  <div className="sm:col-span-2"><dt className="text-ink-faint">当时的错误</dt><dd className="mt-0.5 break-words text-ink">{selected.last_error ?? "前端没有记录到错误"}</dd></div>
                  <div className="sm:col-span-2"><dt className="text-ink-faint">浏览器</dt><dd className="mt-0.5 break-words text-ink">{selected.user_agent || "未提供"}</dd></div>
                </dl>
              </article>
            )}
          </div>
        </div>

        <footer className="flex shrink-0 items-center gap-2 border-t border-line px-5 py-3 text-[0.68rem] leading-relaxed text-ink-faint sm:px-6">
          <CheckMark className="h-3.5 w-3.5" />
          反馈与截图仅供管理员排查产品问题。
        </footer>
      </section>
    </div>
  );
}
