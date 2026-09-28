"use client";

import { useEffect, useRef, useState } from "react";
import { CloseMark } from "@/components/icons";
import {
  createConversationShare, listConversationShares, revokeConversationShare, shareUrl,
  type ConversationShare, type CreatedConversationShare,
} from "@/lib/shares";

const actionClass = "min-h-10 rounded-xl border border-line px-3 py-2 text-sm text-accent-ink transition-colors hover:bg-accent-wash disabled:cursor-not-allowed disabled:opacity-50";

export default function ConversationShareModal({ sessionId, title, generationPending, onClose }: {
  sessionId: string; title: string; generationPending: boolean; onClose: () => void;
}) {
  const [shares, setShares] = useState<ConversationShare[]>([]);
  const [created, setCreated] = useState<CreatedConversationShare | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const dialogRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const closeRef = useRef(onClose);
  const busyRef = useRef(busy);
  closeRef.current = onClose;
  busyRef.current = busy;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    dialog?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busyRef.current) { event.preventDefault(); closeRef.current(); }
      if (event.key !== "Tab") return;
      const controls = Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input, a[href]') ?? []).filter(item => item.getClientRects().length > 0);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) { event.preventDefault(); first?.focus(); }
    }
    dialog?.addEventListener("keydown", onKeyDown);
    return () => { dialog?.removeEventListener("keydown", onKeyDown); previous?.focus(); };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    listConversationShares(sessionId, controller.signal).then(items => {
      if (!controller.signal.aborted) setShares(items);
    }).catch(err => {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "无法加载分享记录。");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [sessionId, retry]);

  async function create() {
    if (busy || generationPending) return;
    setBusy("create"); setError(null); setNotice(null);
    try {
      const item = await createConversationShare(sessionId);
      setCreated(item); setShares(previous => [item, ...previous]);
      setNotice("链接已创建，请复制后发送给需要查看的人。");
    } catch (err) { setError(err instanceof Error ? err.message : "创建分享失败，请重试。"); }
    finally { setBusy(null); }
  }

  async function revoke(item: ConversationShare) {
    if (busy) return;
    setBusy(item.id); setError(null); setNotice(null);
    try {
      await revokeConversationShare(sessionId, item.id);
      setShares(previous => previous.map(share => share.id === item.id ? { ...share, revoked_at: new Date().toISOString() } : share));
      if (created?.id === item.id) setCreated(null);
      setNotice("分享已撤销，链接将无法再次打开。");
    } catch (err) { setError(err instanceof Error ? err.message : "撤销失败，请重试。"); }
    finally { setBusy(null); }
  }

  async function copy() {
    if (!created) return;
    const url = shareUrl(created.token, window.location.origin);
    let copied = false;
    try { if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(url); copied = true; } } catch { /* Use selection below. */ }
    if (!copied) {
      inputRef.current?.focus(); inputRef.current?.select();
      try { copied = document.execCommand("copy"); } catch { /* The selected link can still be copied manually. */ }
    }
    setNotice(copied ? "分享链接已复制。" : "自动复制未成功，已选中链接，请手动复制。");
  }

  const url = created ? shareUrl(created.token, typeof window === "undefined" ? "" : window.location.origin) : "";
  return <div className="zen-overlay-enter fixed inset-0 z-[70] flex items-center justify-center p-3 sm:p-5">
    <div aria-hidden onClick={() => { if (!busy) onClose(); }} className="absolute inset-0 bg-canvas/75 backdrop-blur-md" />
    <section ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="conversation-share-title"
      className="relative flex max-h-[calc(100dvh-2rem)] w-full max-w-xl flex-col overflow-hidden rounded-[28px] border border-line bg-panel depth-panel outline-none">
      <header className="flex items-start gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0 flex-1"><h2 id="conversation-share-title" className="text-base font-medium text-ink">分享对话</h2><p className="mt-1 truncate text-sm text-ink-muted">{title}</p></div>
        <button type="button" onClick={onClose} disabled={!!busy} aria-label="关闭分享" className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-ink-muted hover:bg-raised disabled:opacity-50"><CloseMark className="h-4 w-4" /></button>
      </header>
      <div className="zen-scroll space-y-5 overflow-y-auto px-5 py-5">
        <p className="text-sm leading-relaxed text-ink-muted">持有链接的人无需登录，即可查看这段对话及已保存的回复思考、路由思考、知识片段和中介建议。链接固定保存创建时的内容，后续消息不会自动加入。</p>
        <p className="text-xs leading-relaxed text-ink-faint">可以随时撤销链接；已经查看或复制的内容无法收回。链接只在创建后展示，请及时复制；关闭后仍可在下方撤销。</p>
        {generationPending && <p role="status" className="rounded-xl bg-accent-wash p-3 text-sm text-accent-ink">当前对话仍在生成或保存，请完成后再创建分享。</p>}
        {error && <div role="alert" className="rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}{!loading && !shares.length && !created && <button type="button" onClick={() => setRetry(value => value + 1)} className="ml-2 underline">重新加载</button>}</div>}
        <button type="button" onClick={create} disabled={!!busy || loading || generationPending} className={`${actionClass} w-full bg-accent-wash`}>{busy === "create" ? "正在创建…" : "创建当前对话的分享链接"}</button>
        {created && <section className="space-y-3 rounded-2xl border border-accent-edge bg-accent-wash p-3">
          <label className="block text-sm text-ink">分享链接<input ref={inputRef} aria-label="分享链接" readOnly value={url} onFocus={event => event.currentTarget.select()} className="mt-2 w-full rounded-lg border border-line bg-panel px-3 py-2 text-xs text-ink" /></label>
          <div className="flex flex-wrap gap-2"><button type="button" onClick={copy} className={actionClass}>复制链接</button><a href={url} target="_blank" rel="noopener noreferrer" className={actionClass}>预览分享</a></div>
        </section>}
        {notice && <p role="status" className="text-sm text-accent-ink">{notice}</p>}
        <section aria-labelledby="existing-shares-title" className="space-y-3">
          <h3 id="existing-shares-title" className="text-sm font-medium text-ink">已有分享</h3>
          {loading ? <p role="status" className="text-sm text-ink-muted">正在加载分享记录…</p> : !shares.length ? <p className="text-sm text-ink-faint">这段对话还没有分享记录。</p> : <ul className="space-y-3">{shares.map(item => <li key={item.id} className="flex items-center gap-3 rounded-xl border border-line px-3 py-3">
            <div className="min-w-0 flex-1"><p className="truncate text-sm text-ink">{item.title}</p><p className="mt-1 text-xs text-ink-muted">{new Date(item.created_at).toLocaleString("zh-CN")} · {item.message_count} 条消息</p></div>
            {item.revoked_at ? <span className="text-xs text-ink-faint">已撤销</span> : <button type="button" disabled={!!busy} onClick={() => revoke(item)} className={actionClass} aria-label={`撤销 ${item.title} 的分享`}>{busy === item.id ? "正在撤销…" : "撤销"}</button>}
          </li>)}</ul>}
        </section>
      </div>
    </section>
  </div>;
}
