"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ChatMessage, RoutingMeta } from "@/lib/api";
import MessageMarkdown from "@/components/MessageMarkdown";
import ReasoningDetails from "@/components/ReasoningDetails";
import {
  ArrowUpMark,
  CheckMark,
  ChevronDownMark,
  CopyMark,
  EnvelopeMark,
  EnsoMark,
  IdCardMark,
  KeyMark,
  MenuMark,
  NotebookMark,
  PromptMark,
  ReportMark,
  SandboxMark,
  ShieldMark,
  SignOutMark,
  UserMark,
  UsersMark,
} from "@/components/icons";

/** Ceiling for the auto-growing composer, in px, before it starts scrolling. */
const COMPOSER_MAX_HEIGHT = 160;

const MODULE_ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII"];

/** "module_2" -> "MODULE II". Falls back gracefully for anything unexpected,
 *  so a renamed or unrecognised module still renders instead of vanishing. */
function formatModuleLabel(module: string): string {
  const match = /^module[_-]?(\d+)$/i.exec(module);
  if (!match) return module.toUpperCase();
  const n = Number(match[1]);
  return `MODULE ${MODULE_ROMAN[n] ?? n}`;
}

export default function Chat({
  messages,
  routing,
  busy,
  loading,
  error,
  onSend,
  onStop,
  stopping = false,
  generationNotice,
  generationStartedAt,
  onOpenSidebar,
  headerContent,
  sidebarExpanded = true,
  onOpenAssessment,
  onOpenPushSettings,
  displayName,
  accountUsername,
  displayIdentity,
  accountRole,
  onLogout,
  onChangePassword,
  onOpenEmailSettings,
  onOpenProfile,
  onOpenPromptManager,
  onOpenAccountManager,
  onOpenIssueManager,
  onOpenAdminDailyRecords,
}: {
  messages: ChatMessage[];
  routing: Partial<RoutingMeta>;
  busy: boolean;
  /** A past conversation is being fetched — swap the log for a spinner. */
  loading: boolean;
  error: string | null;
  onSend: (text: string) => void;
  onStop?: () => void;
  stopping?: boolean;
  generationNotice?: string | null;
  generationStartedAt?: number;
  onOpenSidebar: () => void;
  headerContent?: React.ReactNode;
  sidebarExpanded?: boolean;
  /** Opens the daily record on demand — nothing here waits on it. */
  onOpenAssessment: () => void;
  onOpenPushSettings?: () => void;
  /** Nickname, falling back to username — whom this session belongs to. */
  displayName: string;
  /** Globally unique public identity, including the five-digit tag. */
  accountUsername: string;
  /** Public nickname#tag, null until a grandfathered account chooses a tag. */
  displayIdentity: string | null;
  accountRole: "user" | "admin";
  onLogout: () => void;
  onChangePassword: () => void;
  onOpenEmailSettings: () => void;
  onOpenProfile: () => void;
  onOpenPromptManager: () => void;
  onOpenAccountManager: () => void;
  onOpenIssueManager: () => void;
  onOpenAdminDailyRecords?: () => void;
}) {
  const [input, setInput] = useState("");
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  // Scroll the transcript itself. The obvious alternative — a sentinel <div>
  // at the bottom plus scrollIntoView — walks up the tree and scrolls *every*
  // scrollable ancestor on the way, and an `overflow-hidden` box is still
  // programmatically scrollable. That is what was dragging the whole page up
  // and cropping the header off the top of the viewport.
  //
  // Do this synchronously before paint and without smooth scrolling. During a
  // streamed answer `messages` changes for every token batch; repeatedly
  // restarting a smooth animation leaves the viewport behind the growing
  // message and makes its bottom look as if it leaked underneath the composer.
  useLayoutEffect(() => {
    const el = logRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages]);

  // Close the account menu on any outside click. Registered only while it is
  // open, so the app isn't carrying a document-level listener for a popup that
  // is almost always closed.
  useEffect(() => {
    if (!accountMenuOpen) return;
    function onPointerDown(event: PointerEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.closest("[data-account-menu]")) return;
      setAccountMenuOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setAccountMenuOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [accountMenuOpen]);

  // Keep the composer pill-shaped while the message is short, growing it only
  // as far as COMPOSER_MAX_HEIGHT before handing over to its own scrollbar.
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, COMPOSER_MAX_HEIGHT)}px`;
  }, [input]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy || loading) return;
    setInput("");
    onSend(text);
  }

  const canSend = input.trim().length > 0 && !busy && !loading;
  const replyModule = routing.reply_module;
  const nextModule = routing.next_module;
  const displayedModule = nextModule ?? replyModule;
  const moduleChanged = Boolean(
    replyModule && nextModule && replyModule !== nextModule,
  );

  return (
    // min-h-0 matters: a flex item defaults to min-height:auto, which refuses
    // to shrink below its content. Without it a long transcript pushes this
    // box past h-full and the composer walks off the bottom of the screen.
    //
    <div className="workspace-card relative z-10 flex h-full min-h-0 w-full flex-col overflow-clip">
      <header className="workspace-card-header relative z-20 flex min-h-[76px] shrink-0 items-center gap-2 px-3 py-3 sm:gap-4 sm:px-6">
        <button
          type="button"
          onClick={onOpenSidebar}
          aria-label="切换对话侧栏"
          aria-expanded={sidebarExpanded}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-ink-muted transition-colors duration-300 hover:bg-raised hover:text-ink"
        >
          <MenuMark className="h-[18px] w-[18px]" />
        </button>

        <div className="min-w-0 flex-1">
          {headerContent ?? <h1 className="truncate text-[0.92rem] font-semibold tracking-[0.02em] text-ink">对话</h1>}
        </div>

        <div className="ml-auto flex shrink-0 items-center gap-2">
          {accountRole === "admin" && displayedModule && (
            <span
              className="hidden shrink-0 items-center gap-1.5 px-1 text-[0.68rem] font-medium text-accent-ink xl:inline-flex"
              title={`本轮回复：${replyModule ?? "尚无"}；下一轮：${nextModule ?? "判断中"}；来源：${routing.routed_by ?? "unknown"}`}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-accent" />
              {moduleChanged ? (
                <>
                  本轮 {formatModuleLabel(replyModule!)} → 下一轮{" "}
                  {formatModuleLabel(nextModule!)}
                </>
              ) : routing.routing_pending && replyModule ? (
                <>本轮 {formatModuleLabel(replyModule)} · 判断中</>
              ) : (
                <>当前 {formatModuleLabel(displayedModule)}</>
              )}
            </span>
          )}

          {/* Whose session this is, and what can be done about it. The name is
              not decoration: this app holds one person's clinical record, so
              "am I signed in as me?" has to be answerable at a glance. */}
          <div className="relative shrink-0" data-account-menu>
            <button
              type="button"
              onClick={() => setAccountMenuOpen((open) => !open)}
              title={`已登录：${displayName}`}
              aria-label={`账号菜单（当前账号：${displayName}）`}
              aria-haspopup="menu"
              aria-expanded={accountMenuOpen}
              className={`flex min-h-11 items-center gap-1.5 rounded-full px-3 text-xs transition-colors duration-300 ${
                accountRole === "admin"
                  ? "bg-accent-wash text-accent-ink hover:bg-raised"
                  : "text-ink-muted hover:bg-raised hover:text-accent-ink"
              }`}
            >
              {accountRole === "admin" ? (
                <ShieldMark className="h-3.5 w-3.5" />
              ) : (
                <UserMark className="h-3.5 w-3.5" />
              )}
              <span className="hidden max-w-[6rem] truncate sm:inline">
                {displayName}
              </span>
            </button>

            {accountMenuOpen && (
              <div
                role="menu"
                className="absolute right-0 top-full z-40 mt-1.5 w-56 overflow-clip rounded-xl border border-line bg-panel py-1 depth-panel backdrop-blur-2xl"
              >
                <div className="mx-2 mb-1 border-b border-line px-2 py-2">
                  <p className="break-all text-[0.72rem] text-ink">
                    {displayIdentity ?? `${displayName}（尚未设置标签）`}
                  </p>
                  <p className="mt-0.5 break-all font-mono text-[0.64rem] tracking-[0.03em] text-ink-faint">
                    登录账号：{accountUsername}
                  </p>
                </div>
                {onOpenPushSettings && <button type="button" role="menuitem" onClick={()=>{setAccountMenuOpen(false);onOpenPushSettings();}} className="flex min-h-11 w-full items-center gap-2 px-4 py-2.5 text-left text-sm text-ink-muted hover:bg-raised hover:text-ink"><NotebookMark className="h-3.5 w-3.5" />活动后提醒</button>}
                {accountRole === "admin" && (
                  <>
                    {onOpenAdminDailyRecords && <button type="button" role="menuitem" onClick={()=>{setAccountMenuOpen(false);onOpenAdminDailyRecords();}} className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm text-ink-muted hover:bg-raised hover:text-ink"><NotebookMark className="h-4 w-4"/>每日记录数据</button>}
                    <div className="mx-2 mb-2 rounded-lg border border-accent-edge bg-accent-wash px-2.5 py-2 text-accent-ink">
                      <div className="flex items-center justify-between text-[0.68rem]">
                        <span className="flex items-center gap-1.5 font-medium">
                          <ShieldMark className="h-3.5 w-3.5" />
                          管理员控制台
                        </span>
                        <span className="tracking-[0.12em]">ADMIN</span>
                      </div>
                      <p className="mt-1 text-[0.64rem] leading-relaxed text-accent-ink/75">
                        可管理账号权限与全局提示词配置
                      </p>
                    </div>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setAccountMenuOpen(false);
                        onOpenAccountManager();
                      }}
                      className="mx-2 mb-1 flex w-[calc(100%-1rem)] items-start gap-2 rounded-lg px-2 py-2 text-left text-accent-ink transition-colors duration-200 hover:bg-accent-wash"
                    >
                      <UsersMark className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span>
                        <span className="block text-[0.78rem] font-medium">账号与权限管理</span>
                        <span className="mt-0.5 block text-[0.64rem] leading-relaxed text-accent-ink/70">
                          查看账号、授予管理员权限
                        </span>
                      </span>
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setAccountMenuOpen(false);
                        onOpenPromptManager();
                      }}
                      className="mx-2 mb-1 flex w-[calc(100%-1rem)] items-start gap-2 rounded-lg px-2 py-2 text-left text-accent-ink transition-colors duration-200 hover:bg-accent-wash"
                    >
                      <PromptMark className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span>
                        <span className="block text-[0.78rem] font-medium">全局提示词管理</span>
                        <span className="mt-0.5 block text-[0.64rem] leading-relaxed text-accent-ink/70">
                          修改后影响所有用户的下一轮对话
                        </span>
                      </span>
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setAccountMenuOpen(false);
                        onOpenIssueManager();
                      }}
                      className="mx-2 mb-1 flex w-[calc(100%-1rem)] items-start gap-2 rounded-lg px-2 py-2 text-left text-accent-ink transition-colors duration-200 hover:bg-accent-wash"
                    >
                      <ReportMark className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span>
                        <span className="block text-[0.78rem] font-medium">问题反馈管理</span>
                        <span className="mt-0.5 block text-[0.64rem] leading-relaxed text-accent-ink/70">
                          查看用户说明、截图与诊断环境
                        </span>
                      </span>
                    </button>
                  </>
                )}
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setAccountMenuOpen(false);
                    onOpenProfile();
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[0.78rem] text-ink-muted transition-colors duration-200 hover:bg-raised hover:text-ink"
                >
                  <IdCardMark className="h-3.5 w-3.5 shrink-0" />
                  我的档案
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setAccountMenuOpen(false);
                    onOpenEmailSettings();
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[0.78rem] text-ink-muted transition-colors duration-200 hover:bg-raised hover:text-ink"
                >
                  <EnvelopeMark className="h-3.5 w-3.5 shrink-0" />
                  邮箱与找回密码
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setAccountMenuOpen(false);
                    onChangePassword();
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[0.78rem] text-ink-muted transition-colors duration-200 hover:bg-raised hover:text-ink"
                >
                  <KeyMark className="h-3.5 w-3.5 shrink-0" />
                  修改密码
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setAccountMenuOpen(false);
                    onLogout();
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[0.78rem] text-alert-ink transition-colors duration-200 hover:bg-alert-wash"
                >
                  <SignOutMark className="h-3.5 w-3.5 shrink-0" />
                  退出登录
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {loading ? (
        <div className="flex flex-1 items-center justify-center" aria-busy="true">
          <span className="h-6 w-6 animate-spin rounded-full border-2 border-line-strong border-t-accent motion-reduce:animate-none" />
        </div>
      ) : (
        <div
          ref={logRef}
          role="log"
          aria-live="polite"
          className="zen-scroll min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-5 sm:px-8 sm:py-6"
        >
          {messages.length === 0 && routing.routed_by === "admin_sandbox" && (
            <div className="mx-auto mt-[12vh] flex max-w-md flex-col items-center rounded-3xl border border-accent-edge bg-accent-wash px-6 py-7 text-center">
              <span className="grid h-11 w-11 place-items-center rounded-2xl border border-accent-edge bg-panel text-accent-ink">
                <SandboxMark className="h-5 w-5" />
              </span>
              <p className="mt-4 text-xs font-medium tracking-[0.12em] text-accent-ink">
                ADMIN SANDBOX ·{" "}
                {formatModuleLabel(
                  routing.next_module ?? routing.reply_module ?? "",
                )}
              </p>
              <p className="mt-2 text-[0.78rem] leading-relaxed text-ink-muted">
                已从此模块的初始状态开始。这里没有旧对话或进度记忆，测试内容也不会写入真实临床进度。
              </p>
            </div>
          )}
          {messages.map((m, i) => {
            const pending = busy && i === messages.length - 1;
            // A turn that failed before its first delta leaves an empty
            // assistant message behind. Drop it rather than render an empty
            // bubble next to the error banner.
            if (
              !m.content &&
              !m.reasoning_content &&
              !m.routing_reasoning_content &&
              !pending
            ) return null;
            return (
              <MessageRow
                key={i}
                message={m}
                pending={pending}
                generationStartedAt={pending ? generationStartedAt : undefined}
                routingPending={i === messages.length - 1 && routing.routing_pending === true}
                animate={busy && i >= messages.length - 2}
              />
            );
          })}
        </div>
      )}

      {error && (
        <div className="mx-4 mb-2 shrink-0 rounded-2xl border border-alert-edge bg-alert-wash px-4 py-2.5 text-[0.85rem] leading-relaxed text-alert-ink sm:mx-6">
          {error}
        </div>
      )}

      <div className="mx-auto w-full max-w-[58rem] shrink-0 px-4 pb-3 pt-3 sm:px-7 sm:pb-5">
        <form
          onSubmit={handleSubmit}
          className="composer-shell flex items-end gap-2 rounded-3xl py-2 pl-5 pr-2 transition-all duration-300 focus-within:ring-2 focus-within:ring-accent-edge"
        >
          <textarea
            ref={composerRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) handleSubmit(e);
            }}
            placeholder={loading ? "正在加载对话…" : "慢慢说…"}
            rows={1}
            disabled={loading}
            aria-label="Message"
            className="zen-scroll flex-1 resize-none self-center bg-transparent py-2 text-[0.95rem] leading-[1.7] text-ink placeholder:text-ink-faint focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
          />
          <button
            type={busy && onStop ? "button" : "submit"}
            onClick={busy && onStop ? onStop : undefined}
            disabled={busy && onStop ? stopping : !canSend}
            aria-label={busy && onStop ? (stopping ? "正在停止生成" : "停止生成") : busy ? "Waiting for a reply" : "Send"}
            title={busy && onStop ? (stopping ? "正在停止生成" : "停止生成") : "发送"}
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-all duration-500 ease-out ${
              canSend || (busy && onStop)
                ? "bg-accent text-on-accent depth-float hover:brightness-110"
                : "bg-accent-wash text-ink-faint"
            } disabled:cursor-not-allowed`}
          >
            {/* Both states are always mounted and cross-faded, so the swap is a
                dissolve rather than a jump between two different glyphs. */}
            <span className="relative flex h-5 w-5 items-center justify-center">
              <ArrowUpMark
                className={`absolute h-5 w-5 transition-all duration-500 ease-out ${
                  busy ? "scale-75 opacity-0" : "scale-100 opacity-100"
                }`}
              />
              {busy && onStop && !stopping ? <span aria-hidden="true" className="h-3.5 w-3.5 rounded-[3px] bg-current" /> : <span
                className={`absolute h-[18px] w-[18px] animate-spin rounded-full border-2 border-line-strong border-t-accent transition-all duration-500 ease-out motion-reduce:animate-none ${
                  busy ? "scale-100 opacity-100" : "scale-75 opacity-0"
                }`}
              />}
            </span>
          </button>
        </form>

        {generationNotice && <p role="status" className="mt-2 text-center text-xs leading-relaxed text-ink-muted">{generationNotice}</p>}
        {displayedModule === "module_3" && !busy && <div className="mt-2 flex flex-wrap items-center justify-center gap-x-2 text-xs text-ink-muted">
          <span>每日记录：选时间 → 记活动与心情 → 回顾当天</span>
          <button type="button" onClick={onOpenAssessment} className="min-h-9 rounded-lg px-2 font-medium text-accent-ink hover:bg-accent-wash">打开每日记录</button>
        </div>}

        <p className="mt-1.5 text-center text-[0.68rem] leading-relaxed text-ink-faint">
          内容由 AI 生成，仅供参考，不能替代专业建议。
        </p>
      </div>
    </div>
  );
}

function MessageRow({
  message,
  pending,
  generationStartedAt,
  routingPending,
  animate,
}: {
  message: ChatMessage;
  pending: boolean;
  generationStartedAt?: number;
  routingPending: boolean;
  /** Only the newly submitted turn floats in; loaded history stays still. */
  animate: boolean;
}) {
  const isUser = message.role === "user";
  const reasoning = message.reasoning_content?.trim() ?? "";
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (copyResetRef.current) clearTimeout(copyResetRef.current);
    };
  }, []);

  async function handleCopy() {
    try {
      let copied = false;
      if (navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(message.content);
          copied = true;
        } catch {
          // Some embedded browsers expose the API but deny its permission.
          // Fall through to the selection-based path below in that case.
        }
      }

      if (!copied) {
        // Clipboard is unavailable in some embedded or older browsers. Keep a
        // synchronous fallback so the action still works there.
        const textarea = document.createElement("textarea");
        textarea.value = message.content;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand("copy");
        textarea.remove();
        if (!copied) throw new Error("Copy command was rejected");
      }
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }

    if (copyResetRef.current) clearTimeout(copyResetRef.current);
    copyResetRef.current = setTimeout(() => setCopyState("idle"), 1800);
  }

  return (
    <div
      className={`mx-auto flex w-full max-w-5xl items-start gap-3 ${isUser ? "flex-row-reverse" : ""} ${
        animate ? (isUser ? "zen-message-user" : "zen-message-agent") : ""
      }`}
    >
      <Avatar isUser={isUser} />
      <div
        className={`group/message flex min-w-0 max-w-[min(90%,42rem)] items-end gap-1.5 ${
          isUser ? "flex-row-reverse" : ""
        }`}
      >
        <div className="min-w-0">
        {message.content && <div
          data-message-bubble={message.role}
          className={`min-w-0 max-w-[42rem] px-4 py-3 text-[0.95rem] leading-[1.85] tracking-[0.01em] break-words whitespace-pre-wrap sm:px-5 ${
            isUser
              ? "rounded-2xl rounded-tr-md bg-mine text-mine-ink depth-bubble"
              : "rounded-2xl rounded-tl-md bg-agent-bubble text-ink depth-bubble"
          }`}
        >
          {message.role === "assistant" ? <MessageMarkdown text={message.content} /> : message.content}
          {!isUser && (
            <ReasoningDetails message={message} replyPending={pending && !routingPending} routingPending={routingPending} />
          )}
        </div>}
        {!isUser && pending && !routingPending && <TypingDots startedAt={generationStartedAt} hasReasoning={Boolean(reasoning)} hasContent={Boolean(message.content)} />}
        {!isUser && !message.content && <ReasoningDetails message={message} replyPending={pending && !routingPending} routingPending={routingPending} />}
        </div>
        {message.content && (
          <>
            <button
              type="button"
              onClick={handleCopy}
              aria-label={copyState === "copied" ? "已复制消息" : "复制消息"}
              title={copyState === "error" ? "复制失败，请重试" : copyState === "copied" ? "已复制" : "复制"}
              className={`mb-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-transparent transition-all duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-edge sm:opacity-0 sm:group-hover/message:opacity-100 sm:focus-visible:opacity-100 ${
                copyState === "error"
                  ? "border-alert-edge bg-alert-wash text-alert-ink opacity-100"
                  : copyState === "copied"
                    ? "border-accent-edge bg-accent-wash text-accent-ink opacity-100"
                    : "text-ink-faint opacity-60 hover:border-line hover:bg-accent-wash hover:text-accent-ink"
              }`}
            >
              {copyState === "copied" ? (
                <CheckMark className="h-4 w-4" />
              ) : (
                <CopyMark className="h-4 w-4" />
              )}
            </button>
            <span className="sr-only" role="status" aria-live="polite">
              {copyState === "copied" ? "消息已复制到剪贴板" : copyState === "error" ? "消息复制失败" : ""}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

function Avatar({ isUser }: { isUser: boolean }) {
  return (
    <span
      className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${
        isUser
          ? "bg-mine text-accent"
          : "bg-accent-wash text-accent-ink"
      }`}
    >
      {isUser ? (
        <UserMark className="h-4 w-4" />
      ) : (
        <EnsoMark className="h-4 w-4" />
      )}
    </span>
  );
}

function TypingDots({ hasReasoning = false, hasContent = false, startedAt }: { hasReasoning?: boolean; hasContent?: boolean; startedAt?: number }) {
  const [seconds, setSeconds] = useState(() => startedAt ? Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) : 0);
  useEffect(() => {
    const started = startedAt ?? Date.now();
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);
  return (
    <span data-generation-status className="flex min-w-0 items-start gap-2 px-1 py-2" aria-label="Thinking">
      <span aria-hidden="true" className="mt-1.5 flex shrink-0 items-center gap-1.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="zen-breathe h-1.5 w-1.5 rounded-full bg-accent"
            style={{ animationDelay: `${i * 0.18}s` }}
          />
        ))}
      </span>
      <span className="text-xs leading-relaxed text-ink-faint">{seconds >= 20 ? `仍在生成，已等待 ${seconds} 秒；请勿重复提交` : hasContent ? "正在生成回复" : hasReasoning ? "正在深度思考" : "正在准备回复"}</span>
    </span>
  );
}
