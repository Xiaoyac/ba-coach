"use client";

import { useEffect, useRef, useState } from "react";
import { CloseMark } from "@/components/icons";
import {
  createConversationShare, shareUrl,
  type CreatedConversationShare,
} from "@/lib/shares";

const actionClass = "min-h-10 rounded-xl border border-line px-3 py-2 text-sm text-accent-ink transition-colors hover:bg-accent-wash disabled:cursor-not-allowed disabled:opacity-50";

export default function ConversationShareModal({ sessionId, title, generationPending, onClose }: {
  sessionId: string; title: string; generationPending: boolean; onClose: () => void;
}) {
  const [created, setCreated] = useState<CreatedConversationShare | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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

  async function create() {
    if (busy || generationPending) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const item = await createConversationShare(sessionId);
      setCreated(item);
      setNotice("链接已创建，请复制后发送给需要查看的人。");
    } catch (err) { setError(err instanceof Error ? err.message : "创建分享失败，请重试。"); }
    finally { setBusy(false); }
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
        <p className="text-sm leading-relaxed text-ink-muted">持有链接的人无需登录，即可查看对话正文与消息时间。分享不包含思考过程、调试信息、模型请求记录、知识片段或个人档案。链接固定保存创建时的内容，后续消息不会自动加入。</p>
        <p className="text-xs leading-relaxed text-ink-faint">请先检查对话正文是否包含你不希望公开的个人信息。</p>
        <p className="text-xs leading-relaxed text-ink-faint">链接只在创建后展示，请及时复制。关闭后不再显示分享记录。</p>
        {generationPending && <p role="status" className="rounded-xl bg-accent-wash p-3 text-sm text-accent-ink">当前对话仍在生成或保存，请完成后再创建分享。</p>}
        {error && <div role="alert" className="rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}</div>}
        <button type="button" onClick={create} disabled={!!busy || generationPending} className={`${actionClass} w-full bg-accent-wash`}>{busy ? "正在创建…" : "创建当前对话的分享链接"}</button>
        {created && <section className="space-y-3 rounded-2xl border border-accent-edge bg-accent-wash p-3">
          <label className="block text-sm text-ink">分享链接<input ref={inputRef} aria-label="分享链接" readOnly value={url} onFocus={event => event.currentTarget.select()} className="mt-2 w-full rounded-lg border border-line bg-panel px-3 py-2 text-xs text-ink" /></label>
          <div className="flex flex-wrap gap-2"><button type="button" onClick={copy} disabled={!!busy} className={actionClass}>复制链接</button><a href={url} target="_blank" rel="noopener noreferrer" className={actionClass}>预览分享</a></div>
        </section>}
        {notice && <p role="status" className="text-sm text-accent-ink">{notice}</p>}
      </div>
    </section>
  </div>;
}
