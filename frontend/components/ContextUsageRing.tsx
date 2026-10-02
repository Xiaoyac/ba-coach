"use client";

import { useEffect, useRef, useState } from "react";
import { fetchContextUsage, type ContextUsage } from "@/lib/conversations";

export default function ContextUsageRing({ sessionId, busy, version, draft }: {
  sessionId?: string; busy: boolean; version: string; draft: string;
}) {
  const [usage, setUsage] = useState<ContextUsage | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!sessionId || busy) return;
    const controller = new AbortController();
    setFailed(false);
    fetchContextUsage(sessionId, controller.signal).then((result) => {
      if (!controller.signal.aborted) setUsage(result);
    }).catch(() => {
      if (!controller.signal.aborted) { setUsage(null); setFailed(true); }
    });
    return () => controller.abort();
  }, [sessionId, busy, version]);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [open]);
  const draftTokens = draft ? Math.ceil(new TextEncoder().encode(draft).length / 2) : 0;
  const tokens = (usage?.estimated_tokens ?? 0) + draftTokens;
  const ratio = usage ? (usage.token_budget ? tokens / usage.token_budget
    : usage.message_budget ? usage.retained_messages / usage.message_budget : 0) : null;
  const percent = ratio === null ? null : Math.round(ratio * 100);
  const label = percent === null ? "上下文用量暂不可用" : `对话上下文已用约 ${percent}%`;
  const amount = (value: number) => value.toLocaleString("zh-CN");
  return (
    <div ref={root} className="group relative flex h-8 w-8 shrink-0 items-center justify-center">
      <button type="button" aria-label={label} aria-expanded={open}
        title={usage?.token_budget ? `${label} · ${amount(tokens)} / ${amount(usage.token_budget)} tokens（估算）；点击查看详情` : `${label}；点击查看详情`}
        onClick={() => setOpen((value) => !value)}
        className="flex h-8 w-8 items-center justify-center rounded-full text-ink-muted transition-colors hover:bg-accent-wash focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
        <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" className={ratio !== null && ratio >= 0.85 ? "text-amber-600 dark:text-amber-400" : ""}>
          <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="3" opacity="0.2" />
          {ratio !== null && ratio > 0 && <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="3"
            strokeLinecap="round" pathLength="100" strokeDasharray={`${Math.min(100, Math.max(0, ratio * 100))} 100`}
            transform="rotate(-90 12 12)" className="transition-all duration-300 motion-reduce:transition-none" />}
        </svg>
      </button>
      <div role="tooltip" className={`absolute bottom-full right-0 z-30 mb-2 w-64 max-w-[calc(100vw-2rem)] rounded-2xl border border-line bg-panel p-4 text-xs leading-relaxed text-ink shadow-lg ${open ? "block" : "hidden"}`}>
        <p className="mb-2 flex items-center justify-between font-medium"><span>对话上下文</span><span>{percent === null ? "—" : `约 ${percent}%`}</span></p>
        {usage ? <>
          <p>{usage.token_budget ? `${amount(tokens)} / ${amount(usage.token_budget)} tokens（估算）`
            : `保留 ${usage.retained_messages} / ${usage.message_budget} 条消息`}</p>
          <p className="mt-1 text-ink-muted">保留 {usage.retained_messages} 条原文{usage.summarized_messages > 0 ? ` · ${usage.summarized_messages} 条已归入摘要` : ""}</p>
          <p className="mt-2 text-ink-muted">{usage.mode === "compression" ? "按历史压缩预算计算，不含系统提示词和检索内容。达到预算后会尝试压缩，原始聊天记录保留。" : "当前模型使用近期消息窗口。此处显示保留历史的比例。"}</p>
          {draftTokens > 0 && usage.mode === "compression" && <p className="mt-1 text-ink-muted">已计入当前草稿。</p>}
          {busy && <p className="mt-1 text-ink-muted">生成中，完成后更新用量。</p>}
        </> : <p className="text-ink-muted">{failed ? "暂时无法读取，用量会在下次对话后更新。" : busy ? "对话完成后显示用量。" : sessionId ? "正在读取上下文…" : "开始对话后显示用量。"}</p>}
      </div>
    </div>
  );
}
