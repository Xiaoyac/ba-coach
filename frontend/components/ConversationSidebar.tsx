"use client";

import { useEffect, useRef, useState } from "react";
import type { ConversationSummary } from "@/lib/conversations";
import type { SandboxModule } from "@/lib/adminSandbox";
import AdminSandboxControl from "@/components/AdminSandboxControl";
import ThemeToggle from "@/components/ThemeToggle";
import KnowledgeCacheMetric from "@/components/KnowledgeCacheMetric";
import {
  ChatBubbleMark,
  CloseMark,
  EllipsisMark,
  EnsoMark,
  NotebookMark,
  PencilMark,
  PinMark,
  PlusMark,
  ShareMark,
  TrashMark,
} from "@/components/icons";

function relativeTime(iso: string): string {
  const diffMs = new Date(iso).getTime() - Date.now();
  const rtf = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });

  const diffMin = Math.round(diffMs / 60_000);
  if (Math.abs(diffMin) < 1) return "刚刚";
  if (Math.abs(diffMin) < 60) return rtf.format(diffMin, "minute");

  const diffHour = Math.round(diffMin / 60);
  if (Math.abs(diffHour) < 24) return rtf.format(diffHour, "hour");

  const diffDay = Math.round(diffHour / 24);
  if (Math.abs(diffDay) < 30) return rtf.format(diffDay, "day");

  return rtf.format(Math.round(diffDay / 30), "month");
}

/** Matches the server's `title` column so a rename can't fail validation. */
const TITLE_MAX = 80;

export default function ConversationSidebar({
  conversations,
  activeSessionId,
  loading,
  onSelect,
  onNew,
  onDelete,
  onShare,
  onRename,
  onTogglePin,
  onStartSandbox,
  onOpenTestWorkbench,
  onOpenAssessment,
  onOpenGoals,
  onReportIssue,
  reportPreparing = false,
  collapsed = false,
  sandboxBusy = false,
  open,
  onClose,
}: {
  conversations: ConversationSummary[];
  activeSessionId: string | null;
  loading: boolean;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onDelete: (sessionId: string) => void;
  onShare: (sessionId: string) => void;
  onRename: (sessionId: string, title: string) => void;
  onTogglePin: (sessionId: string, pinned: boolean) => void;
  /** Present only for administrators; absence removes the control entirely. */
  onStartSandbox?: (module: SandboxModule) => void;
  onOpenTestWorkbench?: () => void;
  onOpenAssessment?: () => void;
  onOpenGoals?: () => void;
  onReportIssue?: () => void;
  reportPreparing?: boolean;
  collapsed?: boolean;
  sandboxBusy?: boolean;
  /** Mobile-drawer state. Ignored at `md:` and up, where the rail is static. */
  open: boolean;
  onClose: () => void;
}) {
  // At most one row is ever in a given interaction state — opening a menu on
  // another row, or renaming one, drops whatever the previous row was doing
  // rather than stacking two popups over each other.
  const [menuId, setMenuId] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [adminToolsOpen, setAdminToolsOpen] = useState(false);
  const railRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!open || window.matchMedia("(min-width: 768px)").matches) return;
    const previous = document.activeElement as HTMLElement | null;
    const rail = railRef.current;
    const controls = () => Array.from(rail?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]') ?? []).filter(el => el.getClientRects().length > 0);
    controls()[0]?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); }
      if (event.key !== "Tab") return;
      const items = controls();
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    rail?.addEventListener("keydown", trap);
    return () => { rail?.removeEventListener("keydown", trap); previous?.focus(); };
    // The parent closes the same drawer; don't reset focus on every rerender.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Close the action menu on any outside click. Registered only while a menu
  // is actually open, so the app isn't carrying a document-level listener
  // through its entire lifetime for a popup that is usually closed.
  useEffect(() => {
    if (!menuId) return;
    function onPointerDown(event: PointerEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.closest("[data-row-menu]")) return;
      setMenuId(null);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuId(null);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuId]);

  function beginRename(c: ConversationSummary) {
    setMenuId(null);
    setRenamingId(c.session_id);
    setDraftTitle(c.title || "新对话");
  }

  function commitRename(sessionId: string) {
    const next = draftTitle.trim();
    setRenamingId(null);
    // An unchanged or emptied title is a cancel, not a rename — don't spend a
    // request (or let the server 400) on it.
    const previous = conversations.find((c) => c.session_id === sessionId)?.title;
    if (!next || next === previous) return;
    onRename(sessionId, next);
  }

  return (
    <>
      {/* Backdrop: mobile only, and only while the drawer is open. */}
      <div
        aria-hidden
        onClick={onClose}
        className={`fixed inset-0 z-20 bg-canvas/60 backdrop-blur-sm transition-opacity duration-300 md:hidden ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />

      <aside
        ref={railRef}
        role="navigation"
        aria-label="对话历史"
        // The mobile slide is inline, not a `translate-x-*` utility class: some
        // engines ship a compatibility fallback that resets Tailwind v4's
        // `--tw-translate-x` on the unlayered `*` selector, which — being
        // unlayered — outranks any class-based utility regardless of
        // specificity, silently pinning the drawer open. An inline style
        // always wins the cascade, sidestepping that entirely. `md:` clears it
        // back to static positioning, where this value is simply unused.
        style={{ translate: open ? "0" : "-100%" }}
        className={`workspace-rail fixed inset-y-0 left-0 z-30 flex w-[14.5rem] flex-col overflow-clip rounded-r-3xl transition-transform duration-300 ease-out md:static md:z-auto md:h-full md:shrink-0 md:!translate-x-0 md:rounded-none ${open ? "visible" : "invisible"} ${collapsed ? "md:hidden" : "md:visible"}`}
      >
        <div className="shrink-0 px-3 pb-2 pt-5">
          <div className="mb-3 flex items-center gap-2 px-1">
            <span className="grid h-8 w-8 place-items-center rounded-xl bg-accent-wash text-accent">
              <EnsoMark className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <p className="text-[0.75rem] font-semibold tracking-[0.08em] text-ink">BA COACH</p>
              <p className="text-[0.63rem] text-ink-faint">行动练习空间</p>
            </div>
          </div>
          <div className="space-y-1">
          <button type="button" onClick={onNew} className="surface-button mb-3 flex min-h-11 w-full items-center gap-2.5 rounded-xl bg-accent-wash px-3 text-left text-sm font-medium text-accent-ink"><PlusMark className="h-4 w-4" />开启新对话</button>
          {onOpenGoals && <button type="button" onClick={onOpenGoals}
            className="surface-button flex min-h-11 w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm font-medium text-ink-muted">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true" className="h-4 w-4"><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r="1" /></svg>
            我的目标</button>}
          {onOpenAssessment && <button type="button" onClick={onOpenAssessment}
            className="surface-button flex min-h-11 w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm font-medium text-ink-muted">
            <NotebookMark className="h-4 w-4" />记录今日</button>}
          {(onOpenTestWorkbench || onStartSandbox) && <div className="pt-1">
            <button type="button" onClick={() => setAdminToolsOpen((value) => !value)} aria-expanded={adminToolsOpen}
              className="surface-button flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-[0.75rem] text-ink-muted">
              管理工具 <span aria-hidden="true" className="text-ink-faint">{adminToolsOpen ? "−" : "+"}</span>
            </button>
            {adminToolsOpen && <div className="mt-1 space-y-1 border-l border-line pl-2">
              {onOpenTestWorkbench && <button type="button" onClick={onOpenTestWorkbench}
                className="surface-button w-full rounded-xl px-3 py-2 text-left text-[0.78rem] text-accent-ink">测试工作台</button>}
              {onStartSandbox && <AdminSandboxControl busy={sandboxBusy} onStart={onStartSandbox} />}
              <KnowledgeCacheMetric />
            </div>}
          </div>}
          <div className="absolute right-2 top-3 md:hidden">
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭侧栏"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-ink-faint transition-colors duration-300 hover:bg-raised hover:text-ink-muted md:hidden"
            >
              <CloseMark className="h-4 w-4" />
            </button>
          </div>
          </div>
        </div>

        {/* overflow-y-auto would clip the absolutely-positioned action menu,
            so the menu is rendered inside the scroller and the list simply
            scrolls with it — see the row menu below. */}
        <div className="zen-scroll min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-3 pt-3">
          <p className="mb-2 px-2 text-xs text-ink-faint">最近的对话</p>
          {loading ? (
            <div className="space-y-1.5 px-1.5 pt-1">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-11 animate-pulse rounded-xl bg-raised"
                  style={{ animationDelay: `${i * 0.1}s` }}
                />
              ))}
            </div>
          ) : conversations.length === 0 ? (
            <div className="flex flex-col items-center gap-2.5 px-4 pt-10 text-center">
              <ChatBubbleMark className="h-7 w-7 text-ink-faint opacity-50" />
              <p className="text-[0.78rem] leading-relaxed text-ink-faint">
                还没有历史对话
              </p>
            </div>
          ) : (
            conversations.map((c) => {
              const active = c.session_id === activeSessionId;

              // --- Delete confirmation replaces the row entirely ---------
              if (confirmingId === c.session_id) {
                return (
                  <div
                    key={c.session_id}
                    className="flex items-center gap-1.5 rounded-xl bg-alert-wash px-3 py-2.5"
                  >
                    <span className="flex-1 text-[0.78rem] leading-relaxed text-alert-ink">
                      删除对话及其专属记忆、目标？其他对话共用的目标会保留。
                    </span>
                    <button
                      type="button"
                      onClick={() => setConfirmingId(null)}
                      className="shrink-0 rounded-full px-2 py-1 text-[0.72rem] text-ink-muted transition-colors duration-300 hover:bg-raised hover:text-ink"
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setConfirmingId(null);
                        onDelete(c.session_id);
                      }}
                      className="shrink-0 rounded-full bg-alert-edge px-2 py-1 text-[0.72rem] text-alert-ink transition-colors duration-300 hover:opacity-80"
                    >
                      删除
                    </button>
                  </div>
                );
              }

              // --- Inline rename replaces the row entirely ---------------
              if (renamingId === c.session_id) {
                return (
                  <div key={c.session_id} className="px-1 py-1">
                    <input
                      autoFocus
                      value={draftTitle}
                      maxLength={TITLE_MAX}
                      aria-label="重命名对话"
                      onChange={(e) => setDraftTitle(e.target.value)}
                      onBlur={() => commitRename(c.session_id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          commitRename(c.session_id);
                        } else if (e.key === "Escape") {
                          e.preventDefault();
                          setRenamingId(null);
                        }
                      }}
                      className="w-full rounded-xl border border-accent-edge bg-raised px-2.5 py-2 text-[0.82rem] text-ink outline-none"
                    />
                  </div>
                );
              }

              // --- Normal row -------------------------------------------
              const menuOpen = menuId === c.session_id;
              return (
                <div
                  key={c.session_id}
                  data-row-menu={menuOpen ? "" : undefined}
                  className={`group relative flex items-center rounded-xl transition-all duration-300 ${
                    active
                      ? "conversation-row-active bg-accent-wash pl-1 text-accent-ink"
                      : "text-ink-muted hover:bg-raised hover:text-ink"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(c.session_id)}
                    aria-current={active}
                    className="min-w-0 flex-1 py-2.5 pl-3 pr-1.5 text-left"
                  >
                    <span className="flex items-center gap-1.5">
                      {c.pinned && (
                        <PinMark className="h-3 w-3 shrink-0 text-accent" />
                      )}
                      <span className="truncate text-[0.82rem]">
                        {c.title || "新对话"}
                      </span>
                    </span>
                    <span className="block truncate text-[0.68rem] text-ink-faint">
                      {relativeTime(c.updated_at)}
                    </span>
                  </button>

                  <button
                    type="button"
                    onClick={() => setMenuId(menuOpen ? null : c.session_id)}
                    aria-label="更多操作"
                    aria-haspopup="menu"
                    aria-expanded={menuOpen}
                    // Always visible once open, so the trigger doesn't vanish
                    // from under the cursor on a touch device where there is
                    // no hover to keep `group-hover` alive.
                    className={`mr-1.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-ink-faint transition-opacity duration-200 hover:bg-raised hover:text-ink focus-visible:opacity-100 ${
                      menuOpen ? "opacity-100" : "opacity-100 md:opacity-0 md:group-hover:opacity-100"
                    }`}
                  >
                    <EllipsisMark className="h-3.5 w-3.5" />
                  </button>

                  {menuOpen && (
                    <div
                      role="menu"
                      className="absolute right-1.5 top-full z-40 mt-1 w-36 overflow-clip rounded-xl border border-line bg-panel py-1 depth-panel backdrop-blur-2xl"
                    >
                      <MenuItem
                        icon={<PencilMark className="h-3.5 w-3.5" />}
                        label="重命名"
                        onClick={() => beginRename(c)}
                      />
                      <MenuItem
                        icon={<PinMark className="h-3.5 w-3.5" />}
                        label={c.pinned ? "取消置顶" : "置顶"}
                        onClick={() => {
                          setMenuId(null);
                          onTogglePin(c.session_id, !c.pinned);
                        }}
                      />
                      <MenuItem
                        icon={<ShareMark className="h-3.5 w-3.5" />}
                        label="分享"
                        onClick={() => {
                          setMenuId(null);
                          onShare(c.session_id);
                        }}
                      />
                      <MenuItem
                        icon={<TrashMark className="h-3.5 w-3.5" />}
                        label="删除"
                        destructive
                        onClick={() => {
                          setMenuId(null);
                          setConfirmingId(c.session_id);
                        }}
                      />
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
        <div className="flex shrink-0 items-center justify-between gap-2 px-3 pb-3 pt-2">
          {onReportIssue && <button type="button" onClick={onReportIssue} disabled={reportPreparing} data-screenshot-exclude="true" className="surface-button min-h-11 rounded-xl px-3 text-xs text-ink-muted disabled:opacity-50">{reportPreparing ? "正在准备截图…" : "帮助与反馈"}</button>}
          <ThemeToggle inline />
        </div>
      </aside>
    </>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  destructive = false,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={`flex w-full items-center gap-2 px-3 py-2 text-left text-[0.78rem] transition-colors duration-200 ${
        destructive
          ? "text-alert-ink hover:bg-alert-wash"
          : "text-ink-muted hover:bg-raised hover:text-ink"
      }`}
    >
      <span className="shrink-0">{icon}</span>
      {label}
    </button>
  );
}
