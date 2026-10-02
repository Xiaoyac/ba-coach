"use client";

import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { CloseMark } from "@/components/icons";
import { feedbackReasons, fetchMessageFeedback, saveMessageFeedback, type FeedbackInput,
  type FeedbackReason, type MessageFeedback } from "@/lib/messageFeedback";

type FeedbackContextValue = {
  values: Record<number, MessageFeedback>; ready: boolean;
  save: (id: number, input: FeedbackInput) => Promise<void>;
};
const FeedbackContext = createContext<FeedbackContextValue | null>(null);

/** Mounted only in the owner's chat, never on public share pages. */
export function MessageFeedbackProvider({ sessionId, children }: { sessionId?: string; children: ReactNode }) {
  const [values, setValues] = useState<Record<number, MessageFeedback>>({});
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController();
    setValues({}); setReady(false); setError(false);
    if (sessionId) void fetchMessageFeedback(sessionId, controller.signal).then(items => {
      if (current !== generation.current || controller.signal.aborted) return;
      setValues(Object.fromEntries(items.map(item => [item.message_id, item]))); setReady(true);
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [sessionId, retry]);
  async function save(id: number, input: FeedbackInput) {
    const current = generation.current;
    const result = await saveMessageFeedback(id, input);
    if (current !== generation.current) return;
    setValues(previous => {
      const next = { ...previous };
      if (result) next[id] = result; else delete next[id];
      return next;
    });
  }
  return <FeedbackContext.Provider value={{ values, ready, save }}>
    {children}
    {error && <p className="px-1 text-xs text-ink-muted" role="status">暂时无法读取回复评价。
      <button type="button" onClick={() => setRetry(n => n + 1)} className="ml-2 underline underline-offset-4">重试</button>
    </p>}
  </FeedbackContext.Provider>;
}

export function ThumbMark({ down = false }: { down?: boolean }) {
  return <svg viewBox="0 0 24 24" className={`h-[18px] w-[18px] ${down ? "rotate-180" : ""}`} fill="none"
    stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M8 10 12 4c.7-1 2-.5 2 1v4h4a2 2 0 0 1 2 2.4l-1.2 7A2 2 0 0 1 16.8 20H11l-3-2M4 10h4v10H4z" />
  </svg>;
}

export default function ReplyFeedback({ messageId, pending = false, leadingAction }: { messageId?: number | null; pending?: boolean; leadingAction?: ReactNode }) {
  const context = useContext(FeedbackContext);
  const value = messageId != null ? context?.values[messageId] : undefined;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [reasons, setReasons] = useState<FeedbackReason[]>([]);
  const [comment, setComment] = useState("");
  const [notice, setNotice] = useState("");
  const [failed, setFailed] = useState(false);
  const panelId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const downRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (open) closeRef.current?.focus({ preventScroll: true }); }, [open]);
  if (!context || messageId == null || pending) return <div className="mt-1 flex">{leadingAction}</div>;
  function close() { setOpen(false); downRef.current?.focus(); }
  async function submit(input: FeedbackInput, showPanel = false) {
    if (!context?.ready || messageId == null || lock.current) return;
    lock.current = true; setBusy(true); setNotice(""); setFailed(false);
    try {
      await context.save(messageId, input);
      setNotice(input.rating === null ? "已撤销评价" : "谢谢，反馈已保存");
      setOpen(showPanel);
      if (open && !showPanel) downRef.current?.focus({ preventScroll: true });
      if (showPanel) { setReasons([]); setComment(""); }
    } catch (err) {
      setFailed(true); setNotice(err instanceof Error ? err.message : "反馈未保存，请重试");
    } finally { lock.current = false; setBusy(false); }
  }
  return <div className="mt-1" data-reply-feedback>
    <div className="flex flex-wrap items-center gap-0.5" aria-label="评价这条回复">
      {leadingAction}
      {(["up", "down"] as const).map(rating => <button key={rating} type="button"
        ref={rating === "down" ? downRef : undefined}
        aria-label={rating === "up" ? "点赞" : "点踩"} aria-pressed={value?.rating === rating}
        title={value?.rating === rating ? "再次点击撤销" : rating === "up" ? "这条回复有帮助" : "这条回复需要改进"}
        aria-controls={rating === "down" ? panelId : undefined}
        aria-expanded={rating === "down" ? open : undefined}
        disabled={busy || !context.ready}
        onClick={() => void submit({ rating: value?.rating === rating ? null : rating }, rating === "down" && value?.rating !== "down")}
        className={`reply-vote-button ${value?.rating === rating ? "is-selected" : ""}`}>
        <ThumbMark down={rating === "down"} />
      </button>)}
      {value?.rating === "down" && !open && <button type="button" disabled={busy} onClick={() => {
        setReasons(value.reasons); setComment(value.comment); setOpen(true);
      }} className="ml-1 min-h-10 px-2 text-xs text-ink-muted underline decoration-line underline-offset-4 hover:text-ink">补充反馈</button>}
      <span role="status" className={`ml-2 text-xs ${failed ? "text-alert-ink" : "text-ink-muted"}`}>{busy ? "保存中…" : notice}</span>
    </div>
    <div id={panelId} className={`reply-feedback-reveal ${open ? "is-open" : ""}`} inert={!open} aria-hidden={!open}>
      <div className="min-h-0 overflow-clip">
        <form className="reply-feedback-panel" onSubmit={event => { event.preventDefault(); void submit({ rating: "down", reasons, comment }); }}
          onKeyDown={event => { if (event.key === "Escape" && !busy) { event.stopPropagation(); close(); } }}>
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-medium text-ink">这条回复哪里可以更好？</p>
            <button ref={closeRef} type="button" disabled={busy} onClick={close} aria-label="关闭补充反馈"
              className="reply-vote-button"><CloseMark className="h-4 w-4" /></button>
          </div>
          <p className="mb-3 text-xs leading-relaxed text-ink-muted">点踩已记录，以下内容均可选填，仅管理员可见。</p>
          <div className="flex flex-wrap gap-2" role="group" aria-label="反馈原因">
            {(Object.keys(feedbackReasons) as FeedbackReason[]).map(reason => <button key={reason} type="button"
              aria-pressed={reasons.includes(reason)} disabled={busy} className={`reply-feedback-reason ${reasons.includes(reason) ? "is-selected" : ""}`}
              onClick={() => setReasons(previous => previous.includes(reason) ? previous.filter(item => item !== reason) : [...previous, reason])}>
              {feedbackReasons[reason]}
            </button>)}
          </div>
          <label htmlFor={`${panelId}-comment`} className="mt-4 block text-xs text-ink-muted">补充说明（选填）</label>
          <textarea id={`${panelId}-comment`} value={comment} maxLength={1000} disabled={busy} rows={3}
            onChange={event => setComment(event.target.value)} placeholder="例如：我已经回答过这个问题，希望不要再重复问。"
            className="mt-2 w-full resize-y rounded-xl border border-line bg-canvas px-3 py-2 text-sm leading-relaxed text-ink placeholder:text-ink-faint focus:outline-none focus:ring-2 focus:ring-accent-edge" />
          <div className="mt-2 flex items-center justify-between gap-3">
            <span className="text-[11px] text-ink-faint">{comment.length}/1000</span>
            <button type="submit" disabled={busy} className="rounded-full bg-accent-wash px-4 py-2.5 text-xs font-medium text-accent-ink transition-colors hover:bg-raised disabled:opacity-50">{busy ? "保存中…" : "保存补充反馈"}</button>
          </div>
        </form>
      </div>
    </div>
  </div>;
}
