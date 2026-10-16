"use client";

import { useEffect, useRef, useState } from "react";
import { comparePromptVersions, fetchPromptHistory, fetchPromptVersion, type PromptKey, type PromptVersion, type PromptComparison } from "@/lib/adminPrompts";

const actions: Record<string, string> = { baseline: "历史起点", save: "保存修改", restore: "恢复旧版", reset_default: "恢复默认", source_update: "系统默认更新" };
const date = (value: string) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });

function VersionContent({ promptKey, revision }: { promptKey: PromptKey; revision: number }) {
  const [changes, setChanges] = useState<PromptComparison["changes"] | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setChanges(null); setError("");
    const request = revision === 1
      ? fetchPromptVersion(promptKey, revision, controller.signal).then(v => [{ kind: "equal" as const, text: v.content }])
      : comparePromptVersions(promptKey, revision - 1, revision, controller.signal).then(v => v.changes);
    request.then(data => { if (!controller.signal.aborted) setChanges(data); })
      .catch(err => { if (!controller.signal.aborted) setError(err.message); });
    return () => controller.abort();
  }, [promptKey, revision, retry]);
  if (error) return <p role="alert" className="rounded-lg bg-alert-wash p-3 text-xs text-alert-ink">{error}<button type="button" onClick={() => setRetry(v => v + 1)} className="ml-3 rounded border border-current px-2 py-1">重试</button></p>;
  if (!changes) return <p role="status" className="text-xs text-ink-muted">正在读取本次更新…</p>;
  const added = changes.filter(c => c.kind === "insert").reduce((n, c) => n + Array.from(c.text).length, 0);
  const removed = changes.filter(c => c.kind === "delete").reduce((n, c) => n + Array.from(c.text).length, 0);
  return <>
    <p className="mb-3 text-xs leading-5 text-ink-muted">{revision === 1
      ? "这是启用历史记录时的完整快照，没有上一版本；更早的修改过程未被记录。"
      : `版本 ${revision - 1} → 版本 ${revision} · ${added || removed ? `新增 ${added} 字 · 删除 ${removed} 字` : "文字内容未改变"}`}</p>
    {(added > 0 || removed > 0) && <div className="mb-4 flex gap-3 text-xs" aria-label="改动标记说明">
      <span className="rounded border border-accent-ink bg-accent-ink/10 px-2 py-1 text-accent-ink">＋ 新增</span>
      <span className="rounded border border-alert-ink bg-alert-wash px-2 py-1 text-alert-ink">− 删除</span>
    </div>}
    <pre aria-label={`版本 ${revision} 完整更新`} className="whitespace-pre-wrap break-words font-mono text-xs leading-8">{changes.map((c, i) => c.kind === "equal"
      ? <span key={i}>{c.text}</span>
      : <span key={i} data-change={c.kind} aria-label={c.kind === "insert" ? "新增内容" : "删除内容"} className={`rounded border px-1 py-0.5 ${c.kind === "insert" ? "border-accent-ink bg-accent-ink/10 text-accent-ink" : "border-alert-ink bg-alert-wash text-alert-ink"}`} style={{ boxDecorationBreak: "clone", WebkitBoxDecorationBreak: "clone", textDecoration: "none" }}>{c.text}</span>)}</pre>
  </>;
}

export default function PromptHistoryPanel({ promptKey, currentRevision, busy, onRestore }: {
  promptKey: PromptKey; currentRevision: number; busy: boolean; onRestore: (version: number) => void;
}) {
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [moreLoading, setMoreLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  const moreRequest = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    moreRequest.current?.abort(); moreRequest.current = null;
    setLoading(true); setMoreLoading(false); setError(""); setVersions([]); setCursor(null); setExpanded(null);
    fetchPromptHistory(promptKey, undefined, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      setVersions(data.items); setCursor(data.next_before); setExpanded(data.items[0]?.revision ?? null);
    }).catch(err => { if (!controller.signal.aborted) setError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); moreRequest.current?.abort(); };
  }, [promptKey, currentRevision, retry]);
  async function more() {
    if (!cursor || moreRequest.current) return;
    const controller = new AbortController(); moreRequest.current = controller;
    setMoreLoading(true); setError("");
    try {
      const data = await fetchPromptHistory(promptKey, cursor, controller.signal);
      if (!controller.signal.aborted) { setVersions(v => [...v, ...data.items]); setCursor(data.next_before); }
    } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err)); }
    finally { if (!controller.signal.aborted) { setMoreLoading(false); moreRequest.current = null; } }
  }
  return <div className="flex min-h-0 flex-1 flex-col gap-3" aria-label="提示词版本历史">
    <p className="text-xs leading-5 text-ink-muted">每条记录对应一次完整更新，展开后查看相对上一版本的改动。恢复旧版会生成新版本。时间为北京时间。</p>
    {error && <p role="alert" className="rounded-lg bg-alert-wash p-3 text-xs text-alert-ink">{error}<button type="button" onClick={() => setRetry(v => v + 1)} className="ml-3 rounded border border-current px-2 py-1">重试</button></p>}
    {loading ? <p role="status" className="text-sm text-ink-muted">正在读取版本…</p> : <div className="zen-scroll min-h-0 flex-1 overflow-auto">
      <ol className="space-y-3" aria-label="历次完整更新">
        {versions.map(v => <li key={v.revision} className="overflow-hidden rounded-lg border border-line bg-canvas/45">
          <div className="flex items-start gap-2 p-3">
            <button type="button" aria-expanded={expanded === v.revision} aria-controls={`prompt-version-${promptKey}-${v.revision}`} onClick={() => setExpanded(value => value === v.revision ? null : v.revision)} className="min-w-0 flex-1 rounded text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-ink">
              <span className="flex items-center gap-2 text-sm font-medium"><span aria-hidden="true">{expanded === v.revision ? "▾" : "▸"}</span>版本 {v.revision}{v.revision === currentRevision ? " · 当前" : ""}<span className="text-xs font-normal text-ink-muted">{actions[v.action] ?? v.action}</span></span>
              <span className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs leading-5 text-ink-muted"><span>{v.origin === "system" ? "系统" : "管理员"} · {v.actor}</span><time dateTime={v.created_at}>{date(v.created_at)}</time>{v.restored_from && <span>恢复自版本 {v.restored_from}</span>}</span>
            </button>
            {v.revision !== currentRevision && <button type="button" disabled={busy} onClick={() => onRestore(v.revision)} className="shrink-0 rounded-md px-2 py-1 text-xs text-accent-ink hover:bg-raised disabled:opacity-40">恢复此版</button>}
          </div>
          <div id={`prompt-version-${promptKey}-${v.revision}`} hidden={expanded !== v.revision} className="px-4 pb-4 pt-1">
            {expanded === v.revision && <VersionContent key={`${promptKey}-${v.revision}`} promptKey={promptKey} revision={v.revision} />}
          </div>
        </li>)}
      </ol>
      {!versions.length && !error && <p className="text-sm text-ink-muted">暂无版本记录。</p>}
      {cursor && <button type="button" disabled={moreLoading} onClick={() => void more()} className="mt-3 rounded-md bg-raised px-3 py-2 text-xs text-accent-ink">{moreLoading ? "读取中…" : "加载更早版本"}</button>}
    </div>}
  </div>;
}
