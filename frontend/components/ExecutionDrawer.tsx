"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { fetchConversationExecution, type ConversationExecution } from "@/lib/conversations";
import { responseTiming, responseTimingRevision, subscribeResponseTiming } from "@/lib/responseTiming";
import { HistoryMark } from "./icons";
import ExecutionHistory from "./ExecutionHistory";
import ExecutionRun from "./ExecutionRun";

export default function ExecutionDrawer({ sessionId, busy }: { sessionId?: string; busy: boolean }) {
  const [open, setOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [data, setData] = useState<ConversationExecution | null>(null);
  const [error, setError] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const historyButton = useRef<HTMLButtonElement>(null);
  useSyncExternalStore(subscribeResponseTiming, responseTimingRevision, () => 0);
  const latestTiming = responseTiming(sessionId);
  const followLocal = latestTiming && (busy || data?.user_message_id == null
    || (latestTiming.userMessageId !== null && latestTiming.userMessageId >= data.user_message_id));
  const timing = followLocal ? latestTiming : responseTiming(sessionId, data?.user_message_id ?? null);
  // A new send must not show the previous run while the latest poll catches up.
  const current = data && data.session_id === sessionId && (!followLocal || latestTiming.userMessageId === data.user_message_id) ? data : null;
  const currentTurnId = followLocal ? latestTiming.userMessageId : current?.user_message_id ?? null;
  const measured = timing?.firstBodyMs ?? current?.client_first_body_ms;
  const waiting = measured == null && !!timing && (!timing.ended || timing.bodyOffset !== null);
  const startedAt = timing?.startedAt;
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!open || historyOpen || !waiting || startedAt === undefined) return;
    const tick = () => setElapsed(Math.max(0, performance.now() - startedAt));
    tick(); const interval = setInterval(tick, 100);
    return () => clearInterval(interval);
  }, [open, historyOpen, waiting, startedAt]);
  useEffect(() => { setHistoryOpen(false); setData(null); setError(null); }, [sessionId]);
  useEffect(() => { if (open) closeButton.current?.focus(); }, [open]);
  const backToCurrent = () => {
    setHistoryOpen(false);
    requestAnimationFrame(() => historyButton.current?.focus());
  };
  const close = () => { setOpen(false); setHistoryOpen(false); trigger.current?.focus(); };
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (historyOpen) {
        setHistoryOpen(false); requestAnimationFrame(() => historyButton.current?.focus());
      } else { setOpen(false); trigger.current?.focus(); }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [open, historyOpen]);
  useEffect(() => {
    if (!open || !sessionId) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    setData(null); setError(null);
    const poll = async () => {
      if (controller.signal.aborted) return;
      if (document.hidden) { timer = setTimeout(poll, 1500); return; }
      try {
        const result = await fetchConversationExecution(sessionId, null, controller.signal);
        if (controller.signal.aborted) return;
        setData(result); setError(null);
      } catch (reason) {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "暂时无法读取状态");
        const status = (reason as { status?: number })?.status;
        if (status === 401 || status === 403 || status === 404) return;
      }
      timer = setTimeout(poll, 1500);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [open, sessionId, busy]);
  return <>
    <button ref={trigger} type="button" disabled={!sessionId} onClick={() => open ? close() : setOpen(true)}
      aria-label="模型运行状态" aria-expanded={open} aria-controls="execution-drawer"
      className="flex h-9 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-xs text-ink-muted transition-colors hover:bg-raised hover:text-ink disabled:opacity-40">
      <svg className="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="M2 10h4l2-6 4 12 2-6h4" strokeLinecap="round" strokeLinejoin="round" /></svg>
      <span className="hidden sm:inline">运行状态</span>
    </button>
    {open && createPortal(<aside id="execution-drawer" role="dialog" aria-label={historyOpen ? "历史运行记录" : "模型运行状态"} aria-modal="false"
      className="fixed bottom-3 right-3 top-3 z-[90] flex w-[390px] max-w-[calc(100vw-24px)] flex-col overflow-hidden rounded-2xl border border-line bg-panel text-ink shadow-xl sm:bottom-5 sm:right-5 sm:top-5">
      <header className="flex shrink-0 items-center justify-between gap-3 px-5 pb-3 pt-5">
        <div><h2 className="text-base font-semibold">{historyOpen ? "历史运行记录" : "模型运行状态"}</h2><p className="mt-1 text-xs text-ink-muted">当前对话 · 管理员可见</p></div>
        <button ref={closeButton} type="button" onClick={close} aria-label="关闭运行状态" className="flex h-8 w-8 items-center justify-center rounded-lg text-xl text-ink-muted hover:bg-raised">×</button>
      </header>
      {!historyOpen && <div className="flex shrink-0 items-center justify-between gap-2 px-5 pb-4">
        <span className="text-xs font-medium text-ink-muted">当前流程</span>
        <button ref={historyButton} type="button" onClick={() => setHistoryOpen(true)}
          className="flex min-h-10 items-center gap-1.5 rounded-lg border border-line px-3 text-xs text-accent-ink hover:bg-raised focus-visible:outline-2 focus-visible:outline-accent-edge">
          <HistoryMark className="h-3.5 w-3.5" />历史运行记录
        </button>
      </div>}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
        {historyOpen && sessionId ? <ExecutionHistory key={sessionId} sessionId={sessionId} currentTurnId={currentTurnId} onBack={backToCurrent} />
          : <ExecutionRun data={current} error={error} measured={measured} waiting={waiting} elapsed={elapsed} />}
      </div>
    </aside>, document.body)}
  </>;
}
