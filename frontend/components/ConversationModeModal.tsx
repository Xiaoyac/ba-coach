"use client";

import { useEffect, useRef, useState } from "react";
import { replyEffortLabels, replyModeLabels, routingModeLabels, type ConversationReplyEffort, type ConversationReplyMode, type ConversationRoutingMode } from "@/lib/conversations";
import ArchiveSelect from "@/components/ArchiveSelect";

export default function ConversationModeModal({ busy, error, replyEffortOptions, onChoose, onClose }: {
  busy: boolean;
  error: string | null;
  replyEffortOptions?: ConversationReplyEffort[] | null;
  onChoose: (mode: ConversationRoutingMode, replyMode: ConversationReplyMode, replyEffort?: ConversationReplyEffort) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [mode, setMode] = useState<ConversationRoutingMode>("router_code");
  const [replyMode, setReplyMode] = useState<ConversationReplyMode>("standard");
  const [replyEffort, setReplyEffort] = useState<ConversationReplyEffort>("low");
  const efforts: ConversationReplyEffort[] = replyEffortOptions ?? ["low", "high", "max"];
  const selectedEffort = efforts.includes(replyEffort) ? replyEffort : efforts[0];
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);

  return <dialog ref={dialog} aria-labelledby="conversation-mode-title" aria-describedby="conversation-mode-description"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
    className="m-auto max-h-[calc(100dvh-32px)] w-[min(480px,calc(100vw-32px))] max-w-none overflow-y-auto rounded-3xl border border-line bg-panel p-0 text-ink shadow-2xl backdrop:bg-black/40">
    <form onSubmit={event => { event.preventDefault(); if (!busy) onChoose(mode, replyMode, replyMode === "ack_deep" ? selectedEffort : undefined); }} className="p-5 sm:p-7">
      <div className="flex items-start justify-between gap-3">
        <h2 id="conversation-mode-title" className="pt-2 text-lg font-semibold">选择新对话模式</h2>
        <button type="button" disabled={busy} onClick={onClose} aria-label="关闭模式选择" className="grid size-11 shrink-0 place-items-center rounded-full text-2xl text-ink-muted hover:bg-raised disabled:opacity-50">×</button>
      </div>
      <p id="conversation-mode-description" className="mt-2 text-sm leading-6 text-ink-muted">选择后从模块一开始。跳转方式和回复模式创建后固定；思考强度之后仍可调整。</p>
      <fieldset disabled={busy} className="mt-5 space-y-3">
        <legend className="mb-2 text-sm font-medium">模块跳转方式</legend>
        {(["router_code", "router_only"] as const).map(value => <label key={value}
          className={`flex cursor-pointer items-start gap-3 rounded-2xl border p-4 transition-colors ${mode === value ? "border-accent-edge bg-accent-wash" : "border-line bg-raised/40 hover:bg-raised"} ${busy ? "cursor-wait opacity-60" : ""}`}>
          <input type="radio" name="routing-mode" value={value} checked={mode === value} onChange={() => setMode(value)} className="mt-1 size-4 shrink-0 accent-accent" />
          <span><span className="block text-sm font-medium">{routingModeLabels[value]}</span>
            <span className="mt-1 block text-xs leading-5 text-ink-muted">{value === "router_code" ? "Router 判断后，由程序核对模块完成条件。" : "由 Router 决定模块跳转，便于对比对话体验。"}</span></span>
        </label>)}
      </fieldset>
      <p className="mt-4 text-xs leading-5 text-ink-muted">“仅 Router”用于比较模块判断。对话和可核验的草稿仍会保存，模块切换不会自动把目标标记为已确认。</p>
      <fieldset disabled={busy} className="mt-5 space-y-3">
        <legend className="mb-2 text-sm font-medium">回复模式</legend>
        {(["standard", "ack_deep"] as const).map(value => <label key={value}
          className={`flex cursor-pointer items-start gap-3 rounded-2xl border p-4 transition-colors ${replyMode === value ? "border-accent-edge bg-accent-wash" : "border-line bg-raised/40 hover:bg-raised"} ${busy ? "cursor-wait opacity-60" : ""}`}>
          <input type="radio" name="reply-mode" value={value} checked={replyMode === value} onChange={() => setReplyMode(value)} className="mt-1 size-4 shrink-0 accent-accent" />
          <span><span className="block text-sm font-medium">{replyModeLabels[value]}</span>
            <span className="mt-1 block text-xs leading-5 text-ink-muted">{value === "standard" ? "沿用当前回复流程和深度思考设置。" : "先逐字显示简短接话，深度回复读过接话后继续展开；正文到达时恢复正常显示速度，在同一气泡中保存。"}</span></span>
        </label>)}
      </fieldset>
      {replyMode === "ack_deep" && (efforts.length ? <div className="mt-4 rounded-2xl border border-line px-4 py-3">
        <label htmlFor="new-conversation-reply-effort" className="block text-sm font-medium">主回复思考强度{replyEffortOptions == null ? " · Kimi K3" : ""}</label>
        <ArchiveSelect id="new-conversation-reply-effort" label="主回复思考强度" value={selectedEffort} disabled={busy}
          aria-describedby="new-conversation-reply-effort-help"
          onChange={value => setReplyEffort(value as ConversationReplyEffort)}
          options={efforts.map(effort => ({ value: effort, label: replyEffortLabels[effort] }))}
          className="mt-2 w-full" />
        <p id="new-conversation-reply-effort-help" className="mt-2 text-xs leading-5 text-ink-muted">调整思考强度，不是秒数或字数上限。创建后仍可修改。</p>
      </div> : <p className="mt-3 text-xs leading-5 text-ink-muted">当前主回复模型暂不支持手动调整思考强度。</p>)}
      <p className="mt-3 text-xs leading-5 text-ink-muted">实验模式用于对照体验，正式回复可能需要更久；干预规则保持不变。</p>
      {error && <p role="alert" className="mt-4 rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}</p>}
      <div className="mt-6 flex justify-end gap-2">
        <button type="button" disabled={busy} onClick={onClose} className="min-h-11 rounded-xl px-4 text-sm text-ink-muted hover:bg-raised disabled:opacity-50">取消</button>
        <button type="submit" disabled={busy} className="min-h-11 rounded-xl bg-accent px-5 text-sm font-medium text-on-accent disabled:opacity-60">{busy ? "正在创建…" : "创建对话"}</button>
      </div>
    </form>
  </dialog>;
}
