"use client";

import { useEffect, useRef, useState } from "react";
import { routingModeLabels, type ConversationRoutingMode } from "@/lib/conversations";

export default function ConversationModeModal({ busy, error, onChoose, onClose }: {
  busy: boolean;
  error: string | null;
  onChoose: (mode: ConversationRoutingMode) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [mode, setMode] = useState<ConversationRoutingMode>("router_code");
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);

  return <dialog ref={dialog} aria-labelledby="conversation-mode-title" aria-describedby="conversation-mode-description"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
    className="m-auto max-h-[calc(100dvh-32px)] w-[min(480px,calc(100vw-32px))] max-w-none overflow-y-auto rounded-3xl border border-line bg-panel p-0 text-ink shadow-2xl backdrop:bg-black/40">
    <form onSubmit={event => { event.preventDefault(); if (!busy) onChoose(mode); }} className="p-5 sm:p-7">
      <div className="flex items-start justify-between gap-3">
        <h2 id="conversation-mode-title" className="pt-2 text-lg font-semibold">选择新对话模式</h2>
        <button type="button" disabled={busy} onClick={onClose} aria-label="关闭模式选择" className="grid size-11 shrink-0 place-items-center rounded-full text-2xl text-ink-muted hover:bg-raised disabled:opacity-50">×</button>
      </div>
      <p id="conversation-mode-description" className="mt-2 text-sm leading-6 text-ink-muted">选择后从模块一开始。这一模式只用于本段对话，创建后不能切换。</p>
      <fieldset disabled={busy} className="mt-5 space-y-3">
        <legend className="sr-only">模块跳转方式</legend>
        {(["router_code", "router_only"] as const).map(value => <label key={value}
          className={`flex cursor-pointer items-start gap-3 rounded-2xl border p-4 transition-colors ${mode === value ? "border-accent-edge bg-accent-wash" : "border-line bg-raised/40 hover:bg-raised"} ${busy ? "cursor-wait opacity-60" : ""}`}>
          <input type="radio" name="routing-mode" value={value} checked={mode === value} onChange={() => setMode(value)} className="mt-1 size-4 shrink-0 accent-accent" />
          <span><span className="block text-sm font-medium">{routingModeLabels[value]}</span>
            <span className="mt-1 block text-xs leading-5 text-ink-muted">{value === "router_code" ? "Router 判断后，由程序核对模块完成条件。" : "由 Router 决定模块跳转，便于对比对话体验。"}</span></span>
        </label>)}
      </fieldset>
      <p className="mt-4 text-xs leading-5 text-ink-muted">“仅 Router”用于比较模块判断。对话和可核验的草稿仍会保存，模块切换不会自动把目标标记为已确认。</p>
      {error && <p role="alert" className="mt-4 rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}</p>}
      <div className="mt-6 flex justify-end gap-2">
        <button type="button" disabled={busy} onClick={onClose} className="min-h-11 rounded-xl px-4 text-sm text-ink-muted hover:bg-raised disabled:opacity-50">取消</button>
        <button type="submit" disabled={busy} className="min-h-11 rounded-xl bg-accent px-5 text-sm font-medium text-on-accent disabled:opacity-60">{busy ? "正在创建…" : "创建对话"}</button>
      </div>
    </form>
  </dialog>;
}
