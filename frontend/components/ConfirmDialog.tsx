"use client";

import { useEffect } from "react";

export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "取消",
  tone = "accent",
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "accent" | "alert";
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) {
        event.stopPropagation();
        onCancel();
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [busy, onCancel, open]);

  if (!open) return null;

  const alertTone = tone === "alert";
  return (
    <div className="zen-overlay-enter fixed inset-0 z-[80] flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="取消确认"
        disabled={busy}
        onClick={onCancel}
        className="absolute inset-0 bg-canvas/70 backdrop-blur-md"
      />
      <section
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-description"
        className="relative w-full max-w-sm rounded-[26px] border border-line bg-panel/95 p-5 depth-panel backdrop-blur-2xl sm:p-6"
      >
        <div
          className={`mb-4 grid h-10 w-10 place-items-center rounded-2xl border text-sm font-medium ${
            alertTone
              ? "border-alert-edge bg-alert-wash text-alert-ink"
              : "border-accent-edge bg-accent-wash text-accent-ink"
          }`}
          aria-hidden="true"
        >
          {alertTone ? "!" : "?"}
        </div>
        <h2 id="confirm-dialog-title" className="text-[0.95rem] font-medium text-ink">
          {title}
        </h2>
        <p
          id="confirm-dialog-description"
          className="mt-2 text-[0.76rem] leading-6 text-ink-muted"
        >
          {description}
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded-full border border-line px-4 py-2 text-xs text-ink-muted transition-colors duration-300 hover:border-line-strong hover:bg-raised hover:text-ink disabled:opacity-45"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            autoFocus
            disabled={busy}
            onClick={onConfirm}
            className={`min-w-24 rounded-full border px-4 py-2 text-xs font-medium transition-all duration-300 disabled:cursor-wait disabled:opacity-50 ${
              alertTone
                ? "border-alert-edge bg-alert-wash text-alert-ink hover:bg-raised"
                : "border-accent-edge bg-accent-wash text-accent-ink hover:bg-raised"
            }`}
          >
            {busy ? "处理中…" : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}
