"use client";

import { useEffect, useRef, useState } from "react";
import { fetchConversationExecution, fetchExecutionHistory, type ConversationExecution } from "@/lib/conversations";
import { responseTiming } from "@/lib/responseTiming";
import { ArrowLeftMark } from "./icons";
import ExecutionRun from "./ExecutionRun";

type Turn = ConversationExecution["turns"][number];
const dateLabel = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未记录" : date.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  });
};
const backClass = "mb-4 flex min-h-10 items-center gap-1.5 rounded-lg px-2 text-xs text-accent-ink hover:bg-raised focus-visible:outline-2 focus-visible:outline-accent-edge";

function HistoryRun({ sessionId, turn, onBack }: { sessionId: string; turn: Turn; onBack: () => void }) {
  const [data, setData] = useState<ConversationExecution | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const back = useRef<HTMLButtonElement>(null);
  useEffect(() => { back.current?.focus(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError(null);
    fetchConversationExecution(sessionId, turn.id, controller.signal).then(result => {
      if (!controller.signal.aborted) setData(result);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "暂时无法读取记录");
    });
    return () => controller.abort();
  }, [sessionId, turn.id, attempt]);
  return <div onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onBack(); }
  }}>
    <button ref={back} type="button" onClick={onBack} className={backClass}><ArrowLeftMark className="h-3.5 w-3.5" />返回历史列表</button>
    <p className="mb-1 break-words text-sm leading-relaxed text-ink">{turn.preview || "空消息"}</p>
    <p className="mb-5 text-xs text-ink-muted">{dateLabel(turn.created_at)}</p>
    <ExecutionRun data={data} error={error} measured={responseTiming(sessionId, turn.id)?.firstBodyMs ?? data?.client_first_body_ms} history />
    {error && <button type="button" onClick={() => setAttempt(value => value + 1)} className="mt-3 min-h-10 rounded-lg px-3 text-sm text-accent-ink hover:bg-raised">重新读取记录</button>}
  </div>;
}

export default function ExecutionHistory({ sessionId, currentTurnId, onBack }: {
  sessionId: string; currentTurnId: number | null; onBack: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [selected, setSelected] = useState<Turn | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const back = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const lastSelected = useRef<number | null>(null);
  useEffect(() => {
    if (!selected) {
      const row = list.current?.querySelector<HTMLButtonElement>(`[data-history-turn="${lastSelected.current}"]`);
      (row ?? back.current)?.focus();
    }
  }, [selected]);
  useEffect(() => {
    const request = new AbortController(); controller.current = request;
    setTurns([]); setNext(null); setLoading(true); setError(null);
    fetchExecutionHistory(sessionId, null, request.signal).then(page => {
      if (request.signal.aborted) return;
      setTurns(page.turns); setNext(page.next_before_id);
    }).catch(reason => {
      if (!request.signal.aborted) setError(reason instanceof Error ? reason.message : "暂时无法读取历史记录");
    }).finally(() => { if (!request.signal.aborted) setLoading(false); });
    return () => request.abort();
  }, [sessionId, currentTurnId, attempt]);
  async function loadMore() {
    const request = controller.current;
    if (!request || request.signal.aborted || loading || next === null) return;
    setLoading(true); setError(null);
    try {
      const page = await fetchExecutionHistory(sessionId, next, request.signal);
      if (request.signal.aborted) return;
      setTurns(previous => [...previous, ...page.turns.filter(turn => !previous.some(old => old.id === turn.id))]);
      setNext(page.next_before_id);
    } catch (reason) {
      if (!request.signal.aborted) setError(reason instanceof Error ? reason.message : "暂时无法读取更多记录");
    } finally { if (!request.signal.aborted) setLoading(false); }
  }
  if (selected) return <HistoryRun key={`${sessionId}:${selected.id}`} sessionId={sessionId} turn={selected} onBack={() => setSelected(null)} />;
  const older = turns.filter(turn => turn.id !== currentTurnId);
  return <div>
    <button ref={back} type="button" onClick={onBack} className={backClass}><ArrowLeftMark className="h-3.5 w-3.5" />返回当前流程</button>
    <p className="mb-4 text-xs leading-relaxed text-ink-muted">查看以前各轮的流程与耗时。</p>
    <ul ref={list} aria-label="历史运行记录列表" className="divide-y divide-line">
      {older.map(turn => <li key={turn.id}>
        <button type="button" data-history-turn={turn.id} aria-label={`查看运行记录：${turn.preview || "空消息"}`}
          onClick={() => { lastSelected.current = turn.id; setSelected(turn); }}
          className="group flex min-h-20 w-full items-center gap-3 rounded-lg px-2 py-4 text-left hover:bg-raised focus-visible:outline-2 focus-visible:outline-accent-edge">
          <span className="min-w-0 flex-1"><span className="mb-1.5 block text-[11px] tabular-nums text-ink-muted">{dateLabel(turn.created_at)}</span><span className="line-clamp-2 break-words text-sm leading-relaxed text-ink">{turn.preview || "空消息"}</span></span>
          <span aria-hidden="true" className="text-lg text-ink-faint group-hover:text-accent-ink">›</span>
        </button>
      </li>)}
    </ul>
    {loading && <p role="status" className="py-6 text-center text-sm text-ink-muted">正在读取历史运行记录…</p>}
    {!loading && !error && !older.length && <p className="py-10 text-center text-sm text-ink-muted">暂无历史运行记录</p>}
    {error && <div className="py-4 text-sm"><p role="alert" className="text-alert-ink">{error}</p><button type="button" onClick={() => next === null ? setAttempt(value => value + 1) : void loadMore()} className="mt-2 min-h-10 rounded-lg px-3 text-accent-ink hover:bg-raised">重试</button></div>}
    {!loading && !error && next !== null && <button type="button" onClick={() => void loadMore()} className="mt-4 min-h-11 w-full rounded-xl border border-line text-sm text-accent-ink hover:bg-raised">加载更早的记录</button>}
  </div>;
}
