"use client";

import { useEffect, useState } from "react";
import { AuthError, changePassword } from "@/lib/auth";
import { CloseMark } from "@/components/icons";

/** Matches the server's `new_password` floor in ChangePasswordRequest. */
const MIN_LENGTH = 8;

export default function ChangePasswordModal({
  onClose,
}: {
  onClose: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // Checked here as well as on the server so the mismatch is caught before a
  // round trip — the server never sees `confirm` at all.
  const mismatch = confirm.length > 0 && next !== confirm;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || mismatch) return;
    setBusy(true);
    setError(null);
    try {
      await changePassword(current, next);
      setDone(true);
    } catch (err) {
      setError(
        err instanceof AuthError
          ? err.message
          : "连接不上服务器，请确认后端正在运行",
      );
      setBusy(false);
    }
  }

  return (
    <div className="zen-overlay-enter fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        aria-hidden
        onClick={onClose}
        className="absolute inset-0 bg-canvas/70 backdrop-blur-sm"
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-label="修改密码"
        className="relative w-full max-w-sm rounded-[28px] border border-line bg-panel p-6 depth-panel backdrop-blur-2xl"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-full text-ink-faint transition-colors duration-300 hover:bg-raised hover:text-ink-muted"
        >
          <CloseMark className="h-4 w-4" />
        </button>

        <h2 className="mb-5 text-[0.95rem] text-ink">修改密码</h2>

        {done ? (
          <>
            <p className="mb-5 text-[0.82rem] leading-relaxed text-ink-muted">
              密码已更新。其他设备上的登录已全部注销，这台设备不受影响。
            </p>
            <button
              type="button"
              onClick={onClose}
              className="w-full rounded-2xl border border-accent-edge bg-accent-wash py-2.5 text-[0.85rem] text-accent-ink transition-opacity duration-300 hover:opacity-85"
            >
              好
            </button>
          </>
        ) : (
          <form onSubmit={handleSubmit}>
            {/* Lets a password manager associate the entry with the right
                account. Hidden, never edited. */}
            <input type="text" autoComplete="username" className="hidden" readOnly />

            <Field label="当前密码">
              <input
                required
                autoFocus
                type="password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                autoComplete="current-password"
                maxLength={128}
                className={inputClass}
              />
            </Field>

            <Field label="新密码" hint={`至少 ${MIN_LENGTH} 位`}>
              <input
                required
                type="password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                autoComplete="new-password"
                minLength={MIN_LENGTH}
                maxLength={128}
                className={inputClass}
              />
            </Field>

            <Field label="再输入一次新密码">
              <input
                required
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
                maxLength={128}
                aria-invalid={mismatch}
                className={`${inputClass} ${mismatch ? "border-alert-edge" : ""}`}
              />
            </Field>

            {mismatch && (
              <p className="mb-4 text-[0.72rem] text-alert-ink">两次输入不一致</p>
            )}

            {error && (
              <p
                role="alert"
                className="mb-4 rounded-xl bg-alert-wash px-3 py-2.5 text-[0.78rem] leading-relaxed text-alert-ink"
              >
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy || mismatch}
              className="w-full rounded-2xl border border-accent-edge bg-accent-wash py-2.5 text-[0.85rem] text-accent-ink transition-opacity duration-300 hover:opacity-85 disabled:opacity-50"
            >
              {busy ? "请稍候…" : "确认修改"}
            </button>

            <p className="mt-4 text-[0.7rem] leading-relaxed text-ink-faint">
              修改后，其他设备上的登录会被注销。
            </p>
          </form>
        )}
      </div>
    </div>
  );
}

const inputClass =
  "w-full rounded-xl border border-line bg-raised px-3 py-2.5 text-[0.85rem] text-ink outline-none transition-colors duration-300 focus:border-accent-edge";

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="mb-4 block">
      <span className="mb-1.5 block text-[0.78rem] text-ink-muted">{label}</span>
      {hint && (
        <span className="mb-1.5 block text-[0.68rem] text-ink-faint">{hint}</span>
      )}
      {children}
    </label>
  );
}
