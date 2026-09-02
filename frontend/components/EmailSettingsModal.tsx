"use client";

import { useState } from "react";
import { AuthError, resendEmailVerification, saveRecoveryEmail } from "@/lib/auth";

export default function EmailSettingsModal({
  currentEmail,
  verified,
  deliveryAvailable,
  onSaved,
  onClose,
}: {
  currentEmail: string | null;
  verified: boolean;
  deliveryAvailable: boolean;
  onSaved: () => void | Promise<void>;
  onClose: () => void;
}) {
  const [email, setEmail] = useState(currentEmail ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await saveRecoveryEmail(email);
      await onSaved();
      setNotice(deliveryAvailable ? "邮箱已保存；如果尚未验证，验证邮件已经发出。" : "邮箱已保存。邮件服务配置完成后即可发送验证邮件。");
    } catch (err) {
      setError(err instanceof AuthError ? err.message : "邮箱保存失败，请稍后再试");
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setNotice(await resendEmailVerification());
    } catch (err) {
      setError(err instanceof AuthError ? err.message : "发送失败，请稍后再试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="zen-overlay-enter fixed inset-0 z-[90] flex items-center justify-center bg-canvas/75 p-4 backdrop-blur-xl" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section role="dialog" aria-modal="true" aria-labelledby="email-settings-title" className="w-full max-w-md rounded-[28px] border border-line bg-panel p-6 depth-panel">
        <div className="flex items-start justify-between gap-4">
          <div><p className="text-[0.68rem] tracking-[0.16em] text-accent">账号安全</p><h2 id="email-settings-title" className="mt-1.5 text-[1rem] text-ink">邮箱与找回密码</h2></div>
          <button type="button" onClick={onClose} aria-label="关闭" className="rounded-full border border-line px-2.5 py-1 text-ink-faint hover:text-ink">×</button>
        </div>
        <div className="mt-4 rounded-2xl border border-line bg-raised px-4 py-3">
          <p className="text-[0.74rem] text-ink-muted">当前状态</p>
          <p className={`mt-1 text-[0.78rem] ${verified ? "text-accent-ink" : "text-ink-faint"}`}>{verified ? "已验证，可用于找回密码" : currentEmail ? "尚未验证" : "尚未填写邮箱"}</p>
        </div>
        <form onSubmit={save} className="mt-4">
          <label className="text-[0.75rem] text-ink-muted" htmlFor="account-email">邮箱</label>
          <input id="account-email" required type="email" autoComplete="email" maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} className="mt-2 w-full rounded-2xl border border-line bg-raised px-4 py-3 text-[0.84rem] text-ink outline-none focus:border-accent-edge" />
          {error && <p role="alert" className="mt-3 rounded-xl bg-alert-wash px-3 py-2 text-[0.74rem] text-alert-ink">{error}</p>}
          {notice && <p role="status" className="mt-3 rounded-xl border border-accent-edge bg-accent-wash px-3 py-2 text-[0.74rem] leading-relaxed text-accent-ink">{notice}</p>}
          <div className="mt-5 flex flex-col gap-2 sm:flex-row">
            <button type="submit" disabled={busy} className="flex-1 rounded-2xl border border-accent-edge bg-accent-wash py-2.5 text-[0.8rem] text-accent-ink disabled:opacity-50">{busy ? "请稍候…" : "保存邮箱"}</button>
            {currentEmail && !verified && <button type="button" onClick={() => void resend()} disabled={busy || !deliveryAvailable} className="flex-1 rounded-2xl border border-line py-2.5 text-[0.8rem] text-ink-muted disabled:opacity-40">重新发送验证邮件</button>}
          </div>
        </form>
        <p className="mt-4 text-[0.68rem] leading-relaxed text-ink-faint">更换邮箱后需要重新验证。密码重置链接一小时失效，并且只能使用一次。</p>
      </section>
    </div>
  );
}
