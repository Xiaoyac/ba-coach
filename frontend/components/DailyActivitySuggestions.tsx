"use client";

import { useEffect, useRef, useState } from "react";
import { fetchActivityCandidates, type ActivityCandidate } from "@/lib/assessment";

/** Optional suggestions never write into the form until explicitly selected. */
export default function DailyActivitySuggestions({ sessionId, recordDate, addedKeys, onAdd }: {
  sessionId: string;
  recordDate: string;
  addedKeys: string[];
  onAdd: (candidate: ActivityCandidate) => void;
}) {
  const [items, setItems] = useState<ActivityCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [reload, setReload] = useState(0);
  const [confirmedDates, setConfirmedDates] = useState<string[]>([]);
  const [adding, setAdding] = useState<string | null>(null);
  const alive = useRef(true);
  const controllers = useRef(new Set<AbortController>());
  useEffect(() => {
    alive.current = true;
    const pending = controllers.current;
    return () => { alive.current = false; for (const c of pending) c.abort(); };
  }, []);

  async function read(signal: AbortController) {
    controllers.current.add(signal);
    const timer = setTimeout(() => signal.abort(), 8000);
    try { return await fetchActivityCandidates(sessionId, recordDate, signal.signal); }
    finally { clearTimeout(timer); controllers.current.delete(signal); }
  }

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setNotice(null);
    read(controller).then(result => {
      if (!current) return;
      setItems(result.items); setHasMore(result.has_more);
    }).catch(() => {
      if (current) setNotice("暂时没能读取对话活动，你仍可以手动填写或只保存整体评分。");
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; controller.abort(); };
    // The parent remounts this component for each conversation/date.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, recordDate, reload]);

  async function add(candidate: ActivityCandidate) {
    if (adding || addedKeys.includes(candidate.key)) return;
    setAdding(candidate.key); setNotice(null);
    try {
      // A correction may have arrived since opening this panel. Recheck before
      // copying; never silently update a draft the user has already edited.
      const result = await read(new AbortController());
      if (!alive.current) return;
      setItems(result.items); setHasMore(result.has_more);
      const fresh = result.items.find(item => item.key === candidate.key);
      if (!fresh) {
        setNotice("这条活动的来源已更新，请重新核对下面的内容，或手动填写。");
      } else if (!fresh.occurred_on && !confirmedDates.includes(fresh.key)) {
        setNotice("请先确认这项活动发生在所选日期。");
      } else {
        onAdd(fresh);
        setNotice("已加入草稿。请补全活动时间和心情，保存后才会成为记录。");
      }
    } catch {
      if (alive.current) setNotice("暂时无法核对这条活动，尚未加入草稿。你可以重试或手动填写。");
    } finally { if (alive.current) setAdding(null); }
  }

  return <section aria-label="对话中提到的活动" className="rounded-xl border border-accent-edge bg-accent-wash/50 p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h4 className="text-sm font-semibold text-ink">对话中提到的活动</h4>
      <button type="button" disabled={loading || adding !== null} onClick={() => setReload(n => n + 1)} className="min-h-11 rounded-lg px-3 text-sm text-accent-ink hover:bg-accent-wash disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent">重新读取</button>
    </div>
    <p className="text-xs leading-6 text-ink-muted">核对原话后再加入，不会自动保存。这里只显示当前对话中已整理的部分活动。</p>
    {loading ? <p role="status" className="mt-3 text-sm text-ink-muted">正在读取对话活动…</p> : <>
      {items.length === 0 && !notice && <p className="mt-3 text-sm leading-6 text-ink-muted">暂时没有可带入这一天的活动。你可以在下方手动补充。</p>}
      <ul className="mt-3 divide-y divide-line">
        {items.map(item => {
          const added = addedKeys.includes(item.key);
          const dateConfirmed = confirmedDates.includes(item.key);
          return <li key={item.key} className="space-y-2 py-3 first:pt-0 last:pb-0">
            <p className="break-words text-sm font-medium text-ink">{item.activity}</p>
            <blockquote className="border-l-2 border-accent-edge pl-3 text-sm leading-6 text-ink-muted break-words">“{item.source_quote}”</blockquote>
            {item.time_text && !item.time_slot && <p className="text-xs leading-6 text-ink-muted">原话时间：{item.time_text}。加入后请补选时间段。</p>}
            {!item.occurred_on && <label className="flex min-h-11 items-start gap-2 py-2 text-sm leading-6 text-ink">
              <input type="checkbox" disabled={added || adding !== null} checked={dateConfirmed} onChange={e => setConfirmedDates(prev => e.target.checked ? [...prev, item.key] : prev.filter(key => key !== item.key))} className="mt-1 h-4 w-4 shrink-0 accent-accent" />
              <span>日期还不明确，我确认这项活动发生在 {recordDate}。</span>
            </label>}
            <button type="button" disabled={added || adding !== null || (!item.occurred_on && !dateConfirmed)} onClick={() => void add(item)} aria-label={`${added ? "已加入" : "加入草稿"}：${item.activity}`} className="min-h-11 rounded-xl border border-accent-edge bg-sheet px-4 py-2 text-sm font-medium text-accent-ink transition-colors hover:bg-accent-wash disabled:cursor-default disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
              {added ? "已加入草稿" : adding === item.key ? "正在核对…" : "加入草稿"}
            </button>
          </li>;
        })}
      </ul>
      {hasMore && <p className="mt-3 text-xs leading-6 text-ink-muted">活动较多，仅展示部分内容；没有显示的活动可以手动补充。</p>}
    </>}
    {notice && <p role="status" className="mt-3 text-sm leading-6 text-ink-muted">{notice}</p>}
  </section>;
}
