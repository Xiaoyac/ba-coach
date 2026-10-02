"use client";

import { useEffect, useState } from "react";
import MessageRow from "@/components/MessageRow";
import ThemeToggle from "@/components/ThemeToggle";
import { EnsoMark } from "@/components/icons";
import { fetchSharedConversation, ShareRequestError, type SharedConversation } from "@/lib/shares";

export default function SharedConversationView({ token }: { token: string }) {
  const [snapshot, setSnapshot] = useState<SharedConversation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let controller: AbortController | null = null;
    let disposed = false;
    async function load() {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      setSnapshot(null); setLoading(true); setError(null); setUnavailable(false);
      try {
        const result = await fetchSharedConversation(token, request.signal);
        if (!disposed && !request.signal.aborted) setSnapshot(result);
      } catch (err) {
        if (!disposed && !request.signal.aborted) {
          setError(err instanceof Error ? err.message : "暂时无法加载分享，请重试。");
          setUnavailable(err instanceof ShareRequestError && err.status === 404);
        }
      } finally {
        if (!disposed && !request.signal.aborted) setLoading(false);
      }
    }
    void load();
    // Recheck a cached page restored with the Back button or a tab revisited
    // after source-conversation deletion or a tightened public-field policy.
    const onPageShow = (event: PageTransitionEvent) => { if (event.persisted) void load(); };
    const onVisibility = () => { if (document.visibilityState === "visible") void load(); };
    window.addEventListener("pageshow", onPageShow);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      disposed = true; controller?.abort();
      window.removeEventListener("pageshow", onPageShow);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [token, retry]);

  return <main className="flex h-dvh min-h-0 flex-col bg-canvas text-ink">
    <header className="shrink-0 border-b border-line bg-panel/80 px-4 py-4 sm:px-7">
      <div className="mx-auto flex max-w-5xl items-center gap-3">
        <EnsoMark aria-hidden="true" className="h-7 w-7 shrink-0 text-accent-ink" />
        <div className="min-w-0 flex-1">
          <p className="text-xs tracking-wide text-ink-muted">BA Coach · 只读分享</p>
          <h1 className="mt-1 break-words text-base font-medium sm:text-lg">{snapshot?.title || "对话分享"}</h1>
        </div>
        <ThemeToggle inline />
      </div>
    </header>
    <div className="zen-scroll min-h-0 flex-1 overflow-y-auto px-3 py-5 sm:px-6 sm:py-8">
      {loading && <p role="status" className="mx-auto max-w-5xl py-10 text-center text-sm text-ink-muted">正在加载分享对话…</p>}
      {error && <div className="mx-auto max-w-xl rounded-2xl border border-line bg-panel p-6 text-center">
        <p role="alert" className="text-sm text-ink-muted">{error}</p>
        {!unavailable && <button type="button" onClick={() => setRetry(value => value + 1)} className="mt-4 min-h-10 rounded-xl border border-line px-4 py-2 text-sm text-accent-ink hover:bg-accent-wash">重新加载</button>}
      </div>}
      {snapshot && <>
        <div className="mx-auto mb-7 max-w-5xl text-center text-xs leading-relaxed text-ink-muted">
          <p>截至 {new Date(snapshot.created_at).toLocaleString("zh-CN")} · {snapshot.messages.length} 条消息</p>
          <p className="mt-1">{snapshot.snapshot_version === 2 ? "包含分享时已保存的管理员调试详情，后续聊天和详情更新不会加入此分享。" : "仅展示分享时的对话正文与消息时间，后续聊天不会加入此分享。"}</p>
        </div>
        <section aria-label="分享的对话消息" className="space-y-5 sm:space-y-6">
          {snapshot.messages.map((message, index) => <MessageRow
            key={message.id ?? index}
            message={snapshot.snapshot_version === 2 ? message : { id: message.id, role: message.role, content: message.content, created_at: message.created_at }}
            pending={false}
            routingPending={false}
            animate={false}
            showDiagnostics={snapshot.snapshot_version === 2}
            knowledgeSource={{ kind: "snapshot", data: snapshot.snapshot_version === 2 ? message.knowledge_references ?? null : null }}
          />)}
        </section>
        {!snapshot.messages.length && <p className="py-10 text-center text-sm text-ink-muted">这份分享没有保存消息。</p>}
      </>}
    </div>
    <footer className="shrink-0 border-t border-line px-4 py-3 text-center text-xs text-ink-faint">只读对话快照 · 无法在此发送消息或修改原对话</footer>
  </main>;
}
