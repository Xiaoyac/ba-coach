"use client";

import { useEffect, useRef, useState } from "react";
import { CameraMark, CheckMark, CloseMark, ReportMark } from "@/components/icons";
import { captureViewport } from "@/lib/capture";
import { submitIssueReport } from "@/lib/issueReports";

export default function IssueReportModal({
  initialScreenshot,
  initialCaptureError,
  sessionId,
  lastError,
  onClose,
}: {
  initialScreenshot: string | null;
  initialCaptureError: string | null;
  sessionId: string | null;
  lastError: string | null;
  onClose: () => void;
}) {
  const [description, setDescription] = useState("");
  const [screenshot, setScreenshot] = useState(initialScreenshot);
  const [captureError, setCaptureError] = useState(initialCaptureError);
  const [capturing, setCapturing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submittedId, setSubmittedId] = useState<number | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textareaRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !submitting) onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, submitting]);

  async function recapture() {
    setCapturing(true);
    setCaptureError(null);
    setError(null);
    const overlay = overlayRef.current;
    try {
      if (overlay) overlay.style.display = "none";
      await new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      );
      setScreenshot(await captureViewport());
    } catch {
      setCaptureError("自动截图没有成功，你仍然可以只提交文字说明。 ");
    } finally {
      if (overlay) overlay.style.display = "flex";
      setCapturing(false);
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const text = description.trim();
    if (text.length < 3 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await submitIssueReport({
        description: text,
        screenshot_data_url: screenshot,
        page_url: window.location.href,
        session_id: sessionId,
        last_error: lastError,
        user_agent: navigator.userAgent.slice(0, 512),
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        client_online: navigator.onLine,
      });
      setSubmittedId(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      ref={overlayRef}
      data-screenshot-exclude="true"
      className="zen-overlay-enter fixed inset-0 z-[70] flex items-center justify-center p-3 sm:p-5"
    >
      <button
        type="button"
        aria-label="关闭问题反馈"
        onClick={onClose}
        className="absolute inset-0 bg-canvas/75 backdrop-blur-md"
      />

      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="issue-report-title"
        className="relative flex max-h-[calc(100dvh-1.5rem)] w-full max-w-xl flex-col overflow-hidden rounded-[28px] border border-line bg-panel depth-panel backdrop-blur-2xl sm:max-h-[calc(100dvh-2.5rem)]"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-line px-5 py-4 sm:px-6">
          <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-accent-edge bg-accent-wash text-accent-ink">
            <ReportMark className="h-4.5 w-4.5" />
          </span>
          <div className="min-w-0">
            <h2 id="issue-report-title" className="text-base font-medium text-ink">
              我遇到问题
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-ink-faint">
              告诉我们哪里不顺，我们会连同当前环境一起查看。
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

        {submittedId ? (
          <div className="grid min-h-72 place-items-center px-6 py-10 text-center">
            <div>
              <span className="mx-auto grid h-12 w-12 place-items-center rounded-full border border-accent-edge bg-accent-wash text-accent-ink">
                <CheckMark className="h-5 w-5" />
              </span>
              <h3 className="mt-4 text-base font-medium text-ink">已经收到，谢谢你告诉我们</h3>
              <p className="mt-2 text-xs leading-relaxed text-ink-faint">
                反馈编号 #{submittedId}。管理员可以在问题反馈列表中查看并处理。
              </p>
              <button
                type="button"
                onClick={onClose}
                className="mt-6 rounded-2xl border border-accent-edge bg-accent-wash px-5 py-2.5 text-sm text-accent-ink transition-colors duration-300 hover:bg-raised"
              >
                返回对话
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="zen-scroll min-h-0 overflow-y-auto px-5 py-5 sm:px-6">
            <label className="block text-sm font-medium text-ink" htmlFor="issue-description">
              发生了什么？
            </label>
            <p className="mt-1 text-xs leading-relaxed text-ink-faint">
              例如：点击了什么、原本期待看到什么、实际停在了哪里。
            </p>
            <textarea
              ref={textareaRef}
              id="issue-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={5000}
              rows={5}
              placeholder="请描述你遇到的问题…"
              className="zen-scroll mt-3 w-full resize-y rounded-2xl border border-line bg-canvas/45 px-4 py-3 text-sm leading-relaxed text-ink outline-none transition-colors duration-300 placeholder:text-ink-faint focus:border-accent-edge focus:bg-canvas/65"
            />
            <p className="mt-1 text-right text-[0.68rem] text-ink-faint">
              {description.length}/5000
            </p>

            <div className="mt-4 rounded-2xl border border-line bg-raised p-3.5">
              <div className="flex items-center gap-2">
                <CameraMark className="h-4 w-4 shrink-0 text-accent" />
                <p className="text-xs font-medium text-ink">当前页面截图</p>
                <div className="ml-auto flex items-center gap-2">
                  {screenshot && (
                    <button
                      type="button"
                      onClick={() => setScreenshot(null)}
                      className="text-[0.68rem] text-ink-faint transition-colors hover:text-alert-ink"
                    >
                      移除
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={capturing}
                    onClick={() => void recapture()}
                    className="text-[0.68rem] text-accent-ink transition-colors hover:text-ink disabled:cursor-wait disabled:opacity-60"
                  >
                    {capturing ? "正在截取…" : screenshot ? "重新截取" : "添加截图"}
                  </button>
                </div>
              </div>

              {screenshot ? (
                <img
                  src={screenshot}
                  alt="将随问题一起提交的当前页面截图"
                  className="mt-3 max-h-48 w-full rounded-xl border border-line object-contain bg-canvas/50"
                />
              ) : (
                <div className="mt-3 grid h-24 place-items-center rounded-xl border border-dashed border-line text-xs text-ink-faint">
                  暂无截图，将只提交文字说明
                </div>
              )}

              <p className="mt-2 text-[0.68rem] leading-relaxed text-ink-faint">
                截图会在本窗口出现前生成，不会包含此反馈窗口；可能包含当时的对话内容。提交前可预览、移除或重新截取，仅管理员可以查看。
              </p>
              {captureError && (
                <p className="mt-2 text-[0.68rem] leading-relaxed text-alert-ink">
                  {captureError}
                </p>
              )}
            </div>

            {error && (
              <p role="alert" className="mt-4 rounded-xl border border-alert-edge bg-alert-wash px-3 py-2 text-xs text-alert-ink">
                {error}
              </p>
            )}

            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                disabled={submitting}
                className="rounded-2xl px-4 py-2.5 text-sm text-ink-muted transition-colors hover:bg-raised hover:text-ink disabled:opacity-50"
              >
                暂不提交
              </button>
              <button
                type="submit"
                disabled={description.trim().length < 3 || submitting}
                className="rounded-2xl border border-accent-edge bg-accent-wash px-5 py-2.5 text-sm text-accent-ink transition-all duration-300 hover:bg-raised disabled:cursor-not-allowed disabled:opacity-45"
              >
                {submitting ? "正在提交…" : "提交问题"}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
