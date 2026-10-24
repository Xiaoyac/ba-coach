"use client";

import { useEffect, type ReactNode } from "react";

export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "取消",
  tone = "accent",
  busy = false,
  confirmDisabled = false,
  children,
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
  confirmDisabled?: boolean;
  /** Extra content between the description and the buttons, e.g. a field. */
  children?: ReactNode;
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
        className="default-style:absolute default-style:inset-0 default-style:bg-canvas/70 default-style:backdrop-blur-md simple:absolute simple:inset-0 simple:bg-canvas/70 simple:backdrop-blur-sm"
      />
      <section
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-description"
        className="default-style:relative default-style:w-full default-style:max-w-sm default-style:rounded-[26px] default-style:border default-style:border-line default-style:bg-panel/95 default-style:p-5 default-style:depth-panel default-style:backdrop-blur-2xl default-style:sm:p-6 simple:relative simple:w-full simple:max-w-sm simple:rounded-[26px] simple:border simple:border-line simple:bg-panel/95 simple:p-5 simple:depth-panel simple:sm:p-6"
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
        {children}
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
            autoFocus={!children}
            disabled={busy || confirmDisabled}
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
