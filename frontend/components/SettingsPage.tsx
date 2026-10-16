"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { Account } from "@/lib/auth";
import { showSettingsDialog } from "@/lib/settingsDialog";
import { CloseMark, SunMark, IdCardMark, EnvelopeMark, KeyMark, SignOutMark } from "@/components/icons";
import ProfileModal from "@/components/ProfileModal";
import EmailSettingsModal from "@/components/EmailSettingsModal";
import ChangePasswordModal from "@/components/ChangePasswordModal";
import OverallStyleSelector from "@/components/OverallStyleSelector";
import ThemeToggle from "@/components/ThemeToggle";

const sections = [
  { id: "appearance", label: "外观", short: "外观", icon: SunMark },
  { id: "profile", label: "我的档案", short: "档案", icon: IdCardMark },
  { id: "email", label: "邮箱与找回密码", short: "邮箱", icon: EnvelopeMark },
  { id: "password", label: "修改密码", short: "密码", icon: KeyMark },
  { id: "logout", label: "退出登录", short: "退出", icon: SignOutMark },
] as const;
type Section = typeof sections[number]["id"];

export default function SettingsPage({ account, onAccountRefresh, onLogout, active }: {
  account: Account; onAccountRefresh: () => Promise<void>; onLogout: () => Promise<void>; active: boolean;
}) {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const [section, setSection] = useState<Section>("appearance");
  const [visited, setVisited] = useState<Section[]>(["appearance"]);
  const [loggingOut, setLoggingOut] = useState(false);
  const [error, setError] = useState("");
  const pointerStartedOnBackdrop = useRef(false);
  useEffect(() => {
    if (!active || !dialog.current) return;
    return showSettingsDialog(dialog.current);
  }, [active]);
  function select(id: Section) { setSection(id); setVisited(items => items.includes(id) ? items : [...items, id]); }
  // Replacing also works when /settings was loaded directly; Back never leaves
  // the site merely to close this dialog or reopens a dismissed settings page.
  const returnToChat = () => router.replace("/", { scroll: false });
  const name = account.nickname || account.username;
  return <dialog ref={dialog} aria-labelledby="settings-dialog-title" className="settings-window"
    onCancel={event => { event.preventDefault(); returnToChat(); }}
    onPointerDown={event => { pointerStartedOnBackdrop.current = event.target === event.currentTarget; }}
    onClick={event => {
      if (pointerStartedOnBackdrop.current && event.target === event.currentTarget) returnToChat();
      pointerStartedOnBackdrop.current = false;
    }}>
    <section aria-label="设置页面" className="settings-layout">
      <aside className="settings-rail">
        <div className="settings-identity">
          <span aria-hidden="true" className="settings-avatar">{Array.from(name)[0]?.toUpperCase() || "我"}</span>
          <div className="min-w-0"><p className="truncate text-sm font-semibold text-ink">{name}</p><p className="mt-1 truncate text-xs text-ink-faint">个人设置</p></div>
        </div>
        <p className="settings-kicker">用户设置</p>
        <nav aria-label="设置导航" className="settings-nav">
          {sections.map(({ id, label, short, icon: Icon }) => <button key={id} type="button" aria-label={label}
            aria-current={section === id ? "page" : undefined} aria-controls={`settings-${id}`}
            onClick={() => select(id)} title={label} data-destructive={id === "logout" || undefined}
            className="settings-nav-item">
            <Icon className="h-[18px] w-[18px] shrink-0" /><span className="settings-nav-label">{label}</span><span className="settings-nav-short">{short}</span>
          </button>)}
        </nav>
      </aside>
      <div className="settings-pane">
        <header className="settings-titlebar">
          <h1 id="settings-dialog-title" className="text-sm font-medium text-ink-muted">设置<span aria-hidden="true" className="mx-2 text-ink-faint">/</span><span className="text-ink">{sections.find(item => item.id === section)?.label}</span></h1>
          <button type="button" onClick={returnToChat} aria-label="关闭设置，返回对话" title="关闭设置（Esc）" className="settings-close"><CloseMark className="h-5 w-5" /></button>
        </header>
        <div className="settings-content zen-scroll">
          <div className="mx-auto w-full max-w-[680px]">
            {visited.includes("appearance") && <div id="settings-appearance" hidden={section !== "appearance"}>
              <h2 className="flex items-center gap-2 text-2xl font-semibold"><SunMark className="h-5 w-5" />外观</h2>
              <p className="mb-8 mt-2 text-sm leading-6 text-ink-muted">选择你习惯的界面风格与配色。</p>
              {account.role === "admin" && <OverallStyleSelector />}
              <h3 className="mb-3 text-sm font-medium">界面配色</h3>
              <ThemeToggle embedded />
              <p className="mt-5 text-xs leading-6 text-ink-faint">选择会自动保存在当前浏览器。</p>
            </div>}
            {visited.includes("profile") && <div id="settings-profile" hidden={section !== "profile"}><ProfileModal embedded onClose={returnToChat} onSaved={onAccountRefresh} /></div>}
            {visited.includes("email") && <div id="settings-email" hidden={section !== "email"}><EmailSettingsModal embedded currentEmail={account.email} verified={account.email_verified} deliveryAvailable={account.email_delivery_available} onSaved={onAccountRefresh} onClose={returnToChat} /></div>}
            {visited.includes("password") && <div id="settings-password" hidden={section !== "password"}><ChangePasswordModal embedded onClose={returnToChat} /></div>}
            {visited.includes("logout") && <div id="settings-logout" hidden={section !== "logout"} className="max-w-xl">
              <h2 className="text-2xl font-semibold">退出登录</h2>
              <p className="mt-4 text-sm leading-7 text-ink-muted">当前账号：{account.username}</p>
              <p className="mt-2 text-sm leading-7 text-ink-muted">退出后，已保存的对话、档案和每日记录仍会保留。再次登录即可继续。</p>
              {error && <p role="alert" className="mt-4 text-sm text-alert-ink">{error}</p>}
              <button type="button" disabled={loggingOut} onClick={async () => { setLoggingOut(true); setError(""); try { await onLogout(); router.replace("/"); } catch { setError("暂时无法退出，请重试。"); setLoggingOut(false); } }} className="mt-7 min-h-11 rounded-lg bg-alert-wash px-5 text-sm text-alert-ink disabled:opacity-50">{loggingOut ? "正在退出…" : "退出当前账号"}</button>
            </div>}
          </div>
        </div>
      </div>
    </section>
  </dialog>;
}
