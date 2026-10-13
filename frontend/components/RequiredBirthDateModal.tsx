"use client";

import { useEffect, useRef, useState } from "react";
import BirthdayPicker from "@/components/BirthdayPicker";
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
    className="default-style:m-auto default-style:w-[min(440px,calc(100vw-32px))] default-style:max-h-[calc(100dvh-32px)] default-style:overflow-y-auto default-style:rounded-3xl default-style:border-0 default-style:bg-sheet default-style:p-6 default-style:text-ink default-style:shadow-2xl default-style:backdrop:bg-black/40 default-style:sm:p-8 simple:m-auto simple:w-[min(440px,calc(100vw-32px))] simple:max-h-[calc(100dvh-32px)] simple:overflow-y-auto simple:rounded-2xl simple:border-0 simple:bg-sheet simple:p-6 simple:text-ink simple:depth-panel simple:backdrop:bg-black/40 simple:sm:p-8">
    <h1 id="required-birthday-title" className="text-xl font-semibold">补充一下你的生日</h1>
    <p className="mt-3 text-sm leading-7 text-ink-muted">旧资料中的年龄记录为 10 岁，需要你确认出生日期，让建议更贴合实际情况。</p>
    <form onSubmit={submit} className="mt-6 space-y-5" aria-busy={busy}>
      <div className="text-sm"><p className="mb-2">出生日期（必填）</p>
        <BirthdayPicker value={birthday} onChange={setBirthday} disabled={busy} />
      </div>
      <p className="text-xs leading-6 text-ink-faint">请填写真实生日，系统会自动计算年龄。填好后即可继续，之后仍可在「我的档案」中更正。</p>
      {error && <p role="alert" className="rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}</p>}
      <button type="submit" disabled={busy || !birthday} className="min-h-12 w-full rounded-full bg-accent px-4 font-medium text-on-accent disabled:opacity-40">{busy ? "正在保存…" : "保存并继续"}</button>
      <button type="button" disabled={busy} onClick={() => void onLogout()} className="min-h-11 w-full text-sm text-ink-muted disabled:opacity-40">退出登录，稍后填写</button>
    </form>
  </dialog>;
}
