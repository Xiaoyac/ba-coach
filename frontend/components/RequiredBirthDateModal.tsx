"use client";

import { useEffect, useRef, useState } from "react";
import { saveBirthDate, type Account } from "@/lib/auth";

export default function RequiredBirthDateModal({ onSaved, onLogout }: {
  onSaved: (account: Account) => void; onLogout: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const submitting = useRef(false);
  const [birthday, setBirthday] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    // Require an explicit save/logout action. Some Chromium close requests
    // are not cancelable, so also suppress the Escape default action.
    const preventCancel = (event: Event) => event.preventDefault();
    const preventEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") event.preventDefault();
    };
    element.setAttribute("closedby", "none");
    element.addEventListener("cancel", preventCancel);
    element.addEventListener("keydown", preventEscape);
    element.showModal();
    return () => {
      element.removeEventListener("cancel", preventCancel);
      element.removeEventListener("keydown", preventEscape);
      element.close();
    };
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!birthday || submitting.current) return;
    submitting.current = true; setBusy(true); setError(null);
    try { onSaved(await saveBirthDate(birthday)); }
    catch (err) { setError(err instanceof Error ? err.message : "暂时无法保存，请重试。"); }
    finally { submitting.current = false; setBusy(false); }
  }

  return <dialog ref={dialog} aria-labelledby="required-birthday-title" onCancel={event => event.preventDefault()}
    className="m-auto w-[min(440px,calc(100vw-32px))] max-h-[calc(100dvh-32px)] overflow-y-auto rounded-3xl border-0 bg-sheet p-6 text-ink shadow-2xl backdrop:bg-black/40 sm:p-8">
    <h1 id="required-birthday-title" className="text-xl font-semibold">补充一下你的生日</h1>
    <p className="mt-3 text-sm leading-7 text-ink-muted">旧资料中的年龄记录为 10 岁，需要你确认出生日期，让建议更贴合实际情况。</p>
    <form onSubmit={submit} className="mt-6 space-y-5" aria-busy={busy}>
      <label className="block text-sm">出生日期（必填）
        <input type="date" autoComplete="bday" required disabled={busy} value={birthday} onChange={event => setBirthday(event.target.value)}
          className="mt-2 block min-h-12 w-full min-w-0 rounded-xl border border-line bg-raised px-3 text-ink focus:outline-accent" />
      </label>
      <p className="text-xs leading-6 text-ink-faint">请填写真实生日，系统会自动计算年龄。填好后即可继续，之后仍可在「我的档案」中更正。</p>
      {error && <p role="alert" className="rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}</p>}
      <button type="submit" disabled={busy || !birthday} className="min-h-12 w-full rounded-full bg-accent px-4 font-medium text-on-accent disabled:opacity-40">{busy ? "正在保存…" : "保存并继续"}</button>
      <button type="button" disabled={busy} onClick={() => void onLogout()} className="min-h-11 w-full text-sm text-ink-muted disabled:opacity-40">退出登录，稍后填写</button>
    </form>
  </dialog>;
}
