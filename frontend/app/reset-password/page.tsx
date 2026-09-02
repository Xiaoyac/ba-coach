"use client";

import { useEffect, useState } from "react";
import ThemeToggle from "@/components/ThemeToggle";
import { AuthError, resetPassword } from "@/lib/auth";

export default function ResetPasswordPage() {
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    setToken(new URLSearchParams(window.location.search).get("token") ?? "");
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (!token) {
      setError("链接中没有重置凭证，请重新申请");
      return;
    }
    if (password !== confirm) {
      setError("两次输入的密码不一致");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await resetPassword(token, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof AuthError ? err.message : "密码重置失败，请重新申请");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="relative flex h-[100dvh] items-center justify-center overflow-clip bg-canvas p-4">
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-clip">
        <div className="absolute -top-40 left-1/2 h-[34rem] w-[34rem] -translate-x-1/2 rounded-full bg-wash-a blur-[130px]" />
      </div>
      <section className="zen-page-enter relative z-10 w-full max-w-md rounded-[28px] border border-line bg-panel p-6 depth-panel backdrop-blur-2xl">
        <p className="text-[0.68rem] tracking-[0.18em] text-accent">BA COACH · 账号安全</p>
        <h1 className="mt-2 text-[1.08rem] text-ink">设置新密码</h1>
        {done ? (
          <div className="mt-5">
            <p className="rounded-2xl border border-accent-edge bg-accent-wash px-4 py-3 text-[0.8rem] leading-relaxed text-accent-ink">
              密码已经更新，其他设备上的旧登录也已失效。
            </p>
            <a
              href="https://bacoach.xyz/"
              className="mt-5 block w-full rounded-2xl bg-accent-wash py-3 text-center text-[0.84rem] text-accent-ink"
            >
              返回 BA Coach 登录
            </a>
          </div>
        ) : (
          <form onSubmit={submit} className="mt-5 space-y-4">
            <PasswordField label="新密码" value={password} onChange={setPassword} />
            <PasswordField label="再次输入" value={confirm} onChange={setConfirm} />
            {error && (
              <p role="alert" className="rounded-xl bg-alert-wash px-3 py-2.5 text-[0.76rem] text-alert-ink">
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={busy || password.length < 8}
              className="w-full rounded-2xl border border-accent-edge bg-accent-wash py-3 text-[0.84rem] text-accent-ink disabled:opacity-40"
            >
              {busy ? "正在更新…" : "确认更新密码"}
            </button>
            <p className="text-center text-[0.68rem] text-ink-faint">密码至少 8 位；链接只能使用一次。</p>
          </form>
        )}
      </section>
      <ThemeToggle />
    </main>
  );
}

function PasswordField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-[0.76rem] text-ink-muted">
      {label}
      <input
        required
        type="password"
        autoComplete="new-password"
        minLength={8}
        maxLength={128}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 w-full rounded-2xl border border-line bg-raised px-4 py-3 text-[0.86rem] text-ink outline-none focus:border-accent-edge"
      />
    </label>
  );
}
