"use client";

import { useState } from "react";
import {
  AuthError,
  saveRecoveryEmail,
  type Account,
} from "@/lib/auth";

export default function RequiredEmailModal({
  deliveryAvailable,
  onSaved,
}: {
  deliveryAvailable: boolean;
  onSaved: (account: Account) => void;
}) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      onSaved(await saveRecoveryEmail(email));
    } catch (err) {
      setError(
        err instanceof AuthError ? err.message : "邮箱保存失败，请稍后再试",
      );
      setBusy(false);
    }
  }

  return (
    <div className="zen-overlay-enter fixed inset-0 z-[100] flex items-center justify-center bg-canvas/80 p-4 backdrop-blur-xl">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="required-email-title"
        className="w-full max-w-md rounded-[28px] border border-line bg-panel p-6 depth-panel"
      >
        <p className="text-[0.68rem] tracking-[0.18em] text-accent">账号安全</p>
        <h2 id="required-email-title" className="mt-2 text-[1.05rem] text-ink">
          添加找回邮箱
        </h2>
        <p className="mt-2 text-[0.78rem] leading-[1.8] text-ink-muted">
          你的账号建立时还没有邮箱。请现在补充一个常用邮箱；验证完成后，忘记密码时可以通过一次性链接安全找回。
        </p>
        {!deliveryAvailable && (
          <p className="mt-3 rounded-xl border border-line bg-raised px-3 py-2 text-[0.72rem] leading-relaxed text-ink-faint">
            邮件服务正在配置中。你可以先保存邮箱，配置完成后再从账号菜单发送验证邮件。
          </p>
        )}

        <form onSubmit={submit} className="mt-5">
          <label className="block text-[0.76rem] text-ink-muted" htmlFor="required-email">
            邮箱
          </label>
          <input
            id="required-email"
            required
            autoFocus
            type="email"
            autoComplete="email"
            maxLength={320}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="name@example.com"
            className="mt-2 w-full rounded-2xl border border-line bg-raised px-4 py-3 text-[0.86rem] text-ink outline-none focus:border-accent-edge"
          />

          {error && (
            <p role="alert" className="mt-3 rounded-xl bg-alert-wash px-3 py-2 text-[0.75rem] text-alert-ink">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="mt-5 w-full rounded-2xl border border-accent-edge bg-accent-wash py-3 text-[0.84rem] text-accent-ink disabled:opacity-50"
          >
            {busy
              ? "正在保存…"
              : deliveryAvailable
                ? "保存并发送验证邮件"
                : "保存邮箱"}
          </button>
          <p className="mt-3 text-center text-[0.68rem] leading-relaxed text-ink-faint">
            邮箱不会显示给其他用户，也不会用于营销邮件。
          </p>
        </form>
      </section>
    </div>
  );
}
