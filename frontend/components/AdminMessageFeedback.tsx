"use client";
import { useEffect, useState } from "react";
import { feedbackReasons, fetchAdminMessageFeedback, type AdminMessageFeedback as Feedback } from "@/lib/messageFeedback";
import { ThumbMark } from "@/components/MessageFeedback";

export default function AdminMessageFeedback() {
  const [filter, setFilter] = useState<"all" | "up" | "down">("all");
  const [items, setItems] = useState<Feedback[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setItems([]); setCursor(null);
    void fetchAdminMessageFeedback(filter, undefined, controller.signal).then(result => {
      if (!controller.signal.aborted) { setItems(result.items); setCursor(result.next_cursor); }
    }).catch(() => { if (!controller.signal.aborted) setError("无法读取回复评价，请重试"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filter, retry]);
  async function more() {
    if (!cursor || loading) return;
    setLoading(true); setError("");
    try {
      const result = await fetchAdminMessageFeedback(filter, cursor);
      setItems(previous => [...previous, ...result.items]); setCursor(result.next_cursor);
    } catch { setError("无法读取更多评价，请重试"); } finally { setLoading(false); }
  }
  return <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 sm:px-6">
    <div className="mb-4 flex flex-wrap items-center gap-2">
      {(["all", "up", "down"] as const).map(value => <button key={value} type="button" disabled={loading}
        aria-pressed={filter === value} onClick={() => setFilter(value)}
        className={`reply-feedback-reason ${filter === value ? "is-selected" : ""}`}>
        {value === "all" ? "全部评价" : value === "up" ? "有帮助" : "需要改进"}</button>)}
      <span className="ml-auto text-xs text-ink-muted">按回复顺序 · 每条回复保留当前评价</span>
    </div>
    {error && <p role="alert" className="mb-4 text-sm text-alert-ink">{error}
      <button type="button" onClick={() => setRetry(n => n + 1)} className="ml-2 underline">重新加载</button></p>}
    {!loading && !error && items.length === 0 && <p className="py-12 text-center text-sm text-ink-muted">暂时没有这类回复评价</p>}
    <div className="divide-y divide-line">
      {items.map(item => <article key={item.message_id} className="py-4 first:pt-0">
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
          <span className="flex items-center gap-1.5 text-accent-ink"><ThumbMark down={item.rating === "down"} />{item.rating === "up" ? "有帮助" : "需要改进"}</span>
          <span>回复 #{item.message_id} · {item.title}</span>
          <time className="ml-auto">{new Date(item.updated_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })} · 北京时间</time>
        </div>
        <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink">{item.content}</p>
        {item.reasons.length > 0 && <p className="mt-3 text-xs text-accent-ink">{item.reasons.map(reason => feedbackReasons[reason]).join(" · ")}</p>}
        {item.comment && <p className="mt-2 whitespace-pre-wrap break-words border-l-2 border-accent-edge pl-3 text-sm leading-relaxed text-ink-muted">{item.comment}</p>}
        <p className="mt-3 break-all text-[11px] text-ink-faint">{item.model ?? "未记录模型"} · Request ID：{item.request_id ?? "未记录"}</p>
      </article>)}
    </div>
    {loading && <p role="status" className="py-4 text-center text-sm text-ink-muted">正在读取评价…</p>}
    {cursor && <button type="button" disabled={loading} onClick={() => void more()} className="reply-feedback-reason my-3 w-full">加载更多</button>}
  </div>;
}
