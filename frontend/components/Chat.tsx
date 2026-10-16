"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useClaudeStyle, useParticipantStyle } from "@/lib/useParticipantStyle";
import type { ChatMessage, RoutingMeta } from "@/lib/api";
import { type ConversationReplyEffort, type ConversationReplyMode } from "@/lib/conversations";
import MessageRow from "@/components/MessageRow";
import { ReplyEffortSlider, SettingHelp } from "@/components/ConversationControls";
import { MessageFeedbackProvider } from "@/components/MessageFeedback";
import ContextUsageRing from "@/components/ContextUsageRing";
import ExecutionDrawer from "@/components/ExecutionDrawer";
import DailyRecordEntry from "@/components/DailyRecordEntry";
import { restoreFailedText, type FailedChatSend } from "@/lib/chatRecovery";
import GoalFormulationPanel from "@/components/GoalFormulationPanel";
import SettingsLink from "@/components/SettingsLink";
import {
  ArrowUpMark,
  ChevronDownMark,
  MenuMark,
  NotebookMark,
  PinMark,
  PromptMark,
  ReportMark,
  SandboxMark,
  ShareMark,
  ShieldMark,
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
  sessionId,
  messages,
  routing,
  busy,
  replyComplete = false,
  loading,
  error,
  onSend,
  onStop,
  stopping = false,
  generationNotice,
  generationStartedAt,
  onOpenSidebar,
  onShareConversation,
  onToggleMediator,
  onToggleMediatorNode,
  mediatorNodeEnabled = false,
  mediatorEnabled = true,
  mediatorBusy = false,
  replyMode = "standard",
  replyEffort = "low",
  replyEffortOptions = [],
  replyEffortBusy = false,
  onReplyEffortChange,
  replyWaiting = false,
  headerContent,
  sidebarExpanded = true,
  onOpenAssessment,
  onOpenAssessmentHistory,
  assessmentRefreshKey = 0,
  onOpenGoals,
  onSendGoalCard,
  onGoalCardUpdated,
  reuseDraft,
  onOpenPushSettings,
  displayName,
  accountUsername,
  accountRole,
  onOpenPromptManager,
  onOpenAccountManager,
  onOpenIssueManager,
  onOpenAdminDailyRecords,
  onReportIssue,
  reportPreparing = false,
  failedSends = [],
  onRetryFailedSend,
  recoveringSend = false,
  onDismissFailedSend,
}: {
  sessionId?: string;
  messages: ChatMessage[];
  routing: Partial<RoutingMeta>;
  busy: boolean;
  replyComplete?: boolean;
  /** A past conversation is being fetched — swap the log for a spinner. */
  loading: boolean;
  error: string | null;
  onSend: (text: string) => void;
  onStop?: () => void;
  stopping?: boolean;
  generationNotice?: string | null;
  generationStartedAt?: number;
  onOpenSidebar: () => void;
  onShareConversation?: () => void;
  onToggleMediator?: () => void;
  onToggleMediatorNode?: () => void;
  mediatorNodeEnabled?: boolean;
  mediatorEnabled?: boolean;
  mediatorBusy?: boolean;
  replyMode?: ConversationReplyMode;
  replyEffort?: ConversationReplyEffort;
  replyEffortOptions?: ConversationReplyEffort[];
  replyEffortBusy?: boolean;
  onReplyEffortChange?: (effort: ConversationReplyEffort) => void;
  replyWaiting?: boolean;
  headerContent?: React.ReactNode;
  sidebarExpanded?: boolean;
  /** Opens the daily record on demand — nothing here waits on it. */
  onOpenAssessment: () => void;
  onOpenAssessmentHistory?: () => void;
  assessmentRefreshKey?: number;
  reuseDraft?: {text:string; nonce:number} | null;
  onOpenGoals: () => void;
  onOpenCoreGoals?: () => void;
  onSendGoalCard: (text: string, metadata?: Record<string, string>) => Promise<void>;
  onGoalCardUpdated?: () => void;
  onOpenPushSettings?: () => void;
  /** Nickname, falling back to username — whom this session belongs to. */
  displayName: string;
  accountUsername: string;
  /** Public nickname#tag, null until a grandfathered account chooses a tag. */
  accountRole: "user" | "admin";
  onOpenPromptManager: () => void;
  onOpenAccountManager: () => void;
  onOpenIssueManager: () => void;
  onOpenAdminDailyRecords?: () => void;
  onReportIssue?: () => void;
  reportPreparing?: boolean;
  failedSends?: FailedChatSend[];
  onRetryFailedSend?: (item: FailedChatSend) => void;
  recoveringSend?: boolean;
  onDismissFailedSend?: (id: string) => void;
}) {
  const participant = useParticipantStyle();
  const claude = useClaudeStyle();
  const followLatest = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  const logContentRef = useRef<HTMLDivElement>(null);
  const [input, setInput] = useState("");
  const consumedReuse = useRef<number | null>(null);
  useEffect(() => {
    if (!reuseDraft || consumedReuse.current === reuseDraft.nonce) return;
    consumedReuse.current = reuseDraft.nonce;
    setInput(current => current.trim() ? `${current}\n\n${reuseDraft.text}` : reuseDraft.text);
  }, [reuseDraft]);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [headerExpanded, setHeaderExpanded] = useState(false);
  const [goalCardOpen, setGoalCardOpen] = useState(false);
  useEffect(() => { setGoalCardOpen(false); }, [sessionId, accountUsername]);
  const [composerFocused, setComposerFocused] = useState(false);
  const [viewportHeight, setViewportHeight] = useState<number>();
  const workspaceRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const viewport = window.visualViewport;
    const resize = () => {
      const top = workspaceRef.current?.getBoundingClientRect().top ?? 0;
      setViewportHeight(Math.max(160, (viewport?.height ?? window.innerHeight) - Math.max(0, top - (viewport?.offsetTop ?? 0))));
    };
    resize(); viewport?.addEventListener("resize", resize); window.addEventListener("resize", resize);
    return () => { viewport?.removeEventListener("resize", resize); window.removeEventListener("resize", resize); };
  }, []);
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
    if (!participant || followLatest.current) el.scrollTop = el.scrollHeight;
  }, [messages, replyWaiting, replyComplete, participant]);

  useLayoutEffect(() => {
    followLatest.current = true; setShowLatest(false);
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [sessionId, participant, loading]);

  useEffect(() => {
    if (!logRef.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const log = logRef.current;
      if (log && (!participant || followLatest.current)) log.scrollTop = log.scrollHeight;
    });
    observer.observe(logRef.current);
    if (logContentRef.current) observer.observe(logContentRef.current);
    return () => observer.disconnect();
  }, [participant, loading]);

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
    el.style.height = `${Math.min(el.scrollHeight, COMPOSER_MAX_HEIGHT, (viewportHeight ?? 640) / 4)}px`;
  }, [input, viewportHeight]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy || loading || replyEffortBusy || mediatorBusy) return;
    followLatest.current = true; setShowLatest(false);
    setInput("");
    onSend(text);
  }

  const canSend = input.trim().length > 0 && !busy && !loading && !mediatorBusy && !replyEffortBusy;
  const replyModule = routing.reply_module;
  const nextModule = routing.next_module;
  const displayedModule = nextModule ?? replyModule;
  const compactHeader = Boolean(displayedModule && displayedModule !== "module_1");
  const goalShortcut = <button type="button" onClick={() => setGoalCardOpen(true)} disabled={loading}
    aria-label="查看 PA 目标卡" title="PA 目标卡" aria-haspopup="dialog"
    className="inline-flex size-11 shrink-0 items-center justify-center rounded-full text-ink-muted transition-colors hover:bg-raised hover:text-accent-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50">
    <span aria-hidden="true" className="text-xs font-semibold tracking-wide">PA</span>
  </button>;
  useEffect(() => { setHeaderExpanded(false); setAccountMenuOpen(false); setGoalCardOpen(false); }, [displayedModule]);
  const showDailyRecords = !loading && displayedModule === "module_3";
  const moduleChanged = Boolean(
    replyModule && nextModule && replyModule !== nextModule,
  );

  return (
    // min-h-0 matters: a flex item defaults to min-height:auto, which refuses
    // to shrink below its content. Without it a long transcript pushes this
    // box past h-full and the composer walks off the bottom of the screen.
    //
    <div ref={workspaceRef} style={{ maxHeight: viewportHeight }} className="workspace-card relative z-10 flex h-full min-h-0 w-full flex-col overflow-clip">
      {compactHeader && <div className="flex shrink-0 items-center pr-2"><button type="button" aria-expanded={headerExpanded} aria-controls="chat-header-tools" onClick={() => setHeaderExpanded(value => !value)} className="flex min-h-9 min-w-0 flex-1 items-center justify-between gap-2 px-4 text-xs text-ink-muted hover:bg-raised sm:px-6">
        <span>{participant ? "对话工具" : "专心聊，目标、记录和档案都在这里"}</span><span className="flex items-center gap-1 whitespace-nowrap">{headerExpanded ? "收起" : "展开"}<ChevronDownMark className={`h-3.5 w-3.5 transition-transform ${headerExpanded ? "rotate-180" : ""}`} /></span>
      </button>{accountRole === "admin" && <ExecutionDrawer sessionId={sessionId} busy={busy} />}{goalShortcut}</div>}
      <header id="chat-header-tools" hidden={compactHeader && !headerExpanded} className={`workspace-card-header default-style:relative default-style:z-20 default-style:min-h-[76px] default-style:shrink-0 default-style:flex-wrap default-style:items-center default-style:gap-x-2 default-style:gap-y-1 default-style:px-3 default-style:py-3 default-style:sm:gap-4 default-style:sm:px-6 simple:relative simple:z-20 simple:min-h-[76px] simple:shrink-0 simple:flex-wrap simple:items-center simple:gap-x-2 simple:gap-y-1 simple:px-3 simple:py-3 simple:sm:gap-4 simple:sm:px-6 ${compactHeader && !headerExpanded ? "default-style:hidden simple:hidden" : "default-style:flex simple:flex"}`}>
        {accountRole === "admin" && <button
          type="button"
          onClick={onOpenSidebar}
          aria-label="切换对话侧栏"
          aria-expanded={sidebarExpanded}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-ink-muted transition-colors duration-300 hover:bg-raised hover:text-ink"
        >
          <MenuMark className="h-[18px] w-[18px]" />
        </button>}

        {accountRole === "admin" && !compactHeader && <ExecutionDrawer sessionId={sessionId} busy={busy} />}

        <div className="min-w-0 flex-1">
          {headerContent ?? <h1 className="truncate text-[0.92rem] font-semibold tracking-[0.02em] text-ink">对话</h1>}
        </div>

        {accountRole !== "admin" && (
          <nav aria-label="个人快捷入口" className="order-last flex w-full items-center justify-end gap-1 sm:order-none sm:w-auto">
            <SettingsLink />
          </nav>
        )}

        <div className="ml-auto flex shrink-0 items-center gap-2">
          {!compactHeader && goalShortcut}
          {onShareConversation && (
            <button type="button" onClick={onShareConversation} aria-label="分享当前对话" title="分享当前对话" className="inline-flex h-11 min-w-11 items-center justify-center gap-1.5 rounded-full px-2 text-xs text-ink-muted transition-colors hover:bg-raised hover:text-ink sm:px-3">
              <ShareMark className="h-4 w-4 shrink-0" /><span className="hidden sm:inline">分享</span>
            </button>
          )}
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
                className="default-style:absolute default-style:right-0 default-style:top-full default-style:z-40 default-style:mt-1.5 default-style:w-56 default-style:overflow-clip default-style:rounded-xl default-style:border default-style:border-line default-style:bg-panel default-style:py-1 default-style:depth-panel default-style:backdrop-blur-2xl simple:absolute simple:right-0 simple:top-full simple:z-40 simple:mt-1.5 simple:w-56 simple:overflow-clip simple:rounded-xl simple:border simple:border-line simple:bg-panel simple:py-1 simple:depth-panel "
              >
                <div className="mx-2 mb-1 border-b border-line px-2 py-2">
                  <p className="break-all text-[0.72rem] text-ink">
                    {displayName}
                  </p>
                  <p className="mt-0.5 break-all font-mono text-[0.64rem] tracking-[0.03em] text-ink-faint">
                    登录账号：{accountUsername}
                  </p>
                </div>
                {onOpenPushSettings && <button type="button" role="menuitem" onClick={()=>{setAccountMenuOpen(false);onOpenPushSettings();}} className="flex min-h-11 w-full items-center gap-2 px-4 py-2.5 text-left text-sm text-ink-muted hover:bg-raised hover:text-ink"><NotebookMark className="h-3.5 w-3.5" />活动后提醒</button>}
                {accountRole !== "admin" && onReportIssue && (
                  <button type="button" role="menuitem" disabled={reportPreparing} onClick={() => { setAccountMenuOpen(false); onReportIssue(); }} data-screenshot-exclude="true" className="flex min-h-11 w-full items-center gap-2 px-4 py-2.5 text-left text-sm text-ink-muted hover:bg-raised hover:text-ink disabled:opacity-50">
                    <ReportMark className="h-3.5 w-3.5" />{reportPreparing ? "正在准备截图…" : "帮助与反馈"}
                  </button>
                )}
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
                          查看账号、管理注册邀请码
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
                <SettingsLink menu onClick={() => setAccountMenuOpen(false)} />
              </div>
            )}
          </div>
        </div>
      </header>
      {accountRole === "admin" && (onToggleMediator || onToggleMediatorNode || (replyMode === "ack_deep" && onReplyEffortChange && replyEffortOptions.length > 0)) && (
        <div className="relative flex shrink-0 flex-wrap items-center justify-end gap-x-3 gap-y-1 border-b border-line px-3 py-1.5 sm:gap-x-5 sm:px-6" data-testid="conversation-controls" aria-busy={replyEffortBusy || mediatorBusy}>
          {replyMode === "ack_deep" && onReplyEffortChange && replyEffortOptions.length > 0 && <div className="flex items-center gap-1">
            <label htmlFor="conversation-reply-effort" className="hidden text-xs text-ink sm:block">思考强度</label>
            <ReplyEffortSlider key={sessionId} value={replyEffort} options={replyEffortOptions}
              disabled={busy || loading || replyEffortBusy || mediatorBusy} onChange={onReplyEffortChange} />
            <SettingHelp key={sessionId} id="reply-effort-details" label="思考强度说明">
              <p>主回复思考强度：low 更快，high 更充分，xhigh 为最高强度。界面的 xhigh 对应 K3 API 的 max。</p>
              <p className="mt-2">控制思考强度，不是秒数或字数上限。仅修改当前对话，保存后从下一轮生效。</p>
            </SettingHelp>
          </div>}
          {onToggleMediator && <div className="flex items-center gap-1">
            <span id="knowledge-retrieval-label" className="text-xs text-ink"><span className="hidden sm:inline">是否使用</span>新架构</span>
            <button id="knowledge-retrieval-mode" type="button" role="switch" aria-label="是否使用新架构" aria-checked={!mediatorEnabled}
              onClick={onToggleMediator} disabled={busy || loading || replyEffortBusy || mediatorBusy}
              className={`relative h-6 w-10 rounded-full border transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-wait disabled:opacity-50 ${mediatorEnabled ? "border-line bg-raised" : "border-accent bg-accent"}`}>
              <span aria-hidden="true" className={`absolute left-0.5 top-0.5 h-[18px] w-[18px] rounded-full bg-panel shadow-sm transition-transform motion-reduce:transition-none ${mediatorEnabled ? "translate-x-0" : "translate-x-4"}`} />
            </button>
            <SettingHelp key={sessionId} id="knowledge-retrieval-details" label="知识检索架构说明">
              <p>关闭 · 老架构：使用目录检索。</p>
              <p className="mt-2">开启 · 新架构：使用混合检索与重排。两种架构是否调用中介，由旁边的“是否启用中介节点”独立控制。</p>
              <p className="mt-2">仅修改当前对话，保存后从下一轮生效。</p>
            </SettingHelp>
          </div>}
          {accountRole === "admin" && onToggleMediatorNode && <div className="flex items-center gap-2 whitespace-nowrap">
            <span className="text-xs text-ink">是否启用中介节点</span>
            <button type="button" role="switch" aria-label="是否启用中介节点" aria-checked={mediatorNodeEnabled}
              onClick={onToggleMediatorNode} disabled={busy || loading || replyEffortBusy || mediatorBusy}
              className={`relative h-6 w-10 rounded-full border transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-wait disabled:opacity-50 ${mediatorNodeEnabled ? "border-accent bg-accent" : "border-line bg-raised"}`}>
              <span aria-hidden="true" className={`absolute left-0.5 top-0.5 h-[18px] w-[18px] rounded-full bg-panel shadow-sm transition-transform motion-reduce:transition-none ${mediatorNodeEnabled ? "translate-x-4" : "translate-x-0"}`} />
            </button>
            <SettingHelp key={sessionId} id="knowledge-mediator-node-details" label="中介节点说明">
              <p>默认关闭。关闭时，检索材料直接供主回复使用；开启时，由中介筛选材料并提供使用建议。</p>
              <p className="mt-2">独立于新旧架构，仅修改当前对话，从下一轮生效。普通用户固定使用新架构并启用中介。</p>
            </SettingHelp>
          </div>}
          <span role="status" className="sr-only">{replyEffortBusy || mediatorBusy ? "保存中…" : ""}</span>
        </div>
      )}

      <GoalFormulationPanel key={`${accountUsername}:${sessionId ?? "new"}`} sessionId={sessionId}
        confirmationResult={routing.goal_card_confirmation}
        viewOpen={goalCardOpen} onCloseView={() => setGoalCardOpen(false)} composerFocused={composerFocused}
        accountKey={accountUsername} module={routing.next_module ?? routing.reply_module} busy={busy} loading={loading}
        refreshKey={`${messages.length}:${messages.at(-1)?.id ?? ""}:${busy}`}
        onSend={onSendGoalCard} onOpenGoals={onOpenGoals} onUpdated={onGoalCardUpdated} />

      {loading ? (
        <div className="flex flex-1 items-center justify-center" aria-busy="true">
          <span className="h-6 w-6 animate-spin rounded-full border-2 border-line-strong border-t-accent motion-reduce:animate-none" />
        </div>
      ) : (
        <div
          ref={logRef}
          role="log"
          aria-live="polite"
          onScroll={() => {
            if (!participant || !logRef.current) return;
            const log = logRef.current;
            const nearBottom = log.scrollHeight - log.clientHeight - log.scrollTop < 64;
            followLatest.current = nearBottom; setShowLatest(!nearBottom);
          }}
          className="participant-chat-log zen-scroll min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-8 sm:py-6"
        ><div ref={logContentRef} className="space-y-5">
          {accountRole === "admin" && messages.length === 0 && routing.routed_by === "admin_sandbox" && (
            <div className="default-style:mx-auto default-style:mt-[12vh] default-style:flex default-style:max-w-md default-style:flex-col default-style:items-center default-style:rounded-3xl default-style:border default-style:border-accent-edge default-style:bg-accent-wash default-style:px-6 default-style:py-7 default-style:text-center simple:mx-auto simple:mt-[12vh] simple:flex simple:max-w-md simple:flex-col simple:items-center simple:rounded-2xl simple:border simple:border-accent-edge simple:bg-accent-wash simple:px-6 simple:py-7 simple:text-center">
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
          <MessageFeedbackProvider key={sessionId} sessionId={sessionId}>
          {messages.map((m, i) => {
            const pending = busy && !replyComplete && !error && i === messages.length - 1;
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
                sessionId={sessionId}
                message={m}
                pending={pending}
                replyWaiting={pending && replyWaiting}
                generationStartedAt={pending ? generationStartedAt : undefined}
                routingPending={i === messages.length - 1 && routing.routing_pending === true}
                animate={busy && i >= messages.length - 2}
                showDiagnostics={accountRole === "admin"}
              />
            );
          })}
          </MessageFeedbackProvider>
          {busy && replyComplete && !error && <p role="status" className="px-4 py-2 text-xs text-ink-muted">{claude ? "正在保存…" : "回复已生成，正在确认保存结果…"}</p>}
        </div></div>
      )}
      {participant && showLatest && !loading && <button type="button" className="mx-auto min-h-9 rounded-full bg-raised px-4 text-xs text-ink-muted"
        onClick={() => { followLatest.current = true; setShowLatest(false); if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }}>回到最新回复 ↓</button>}

      {error && (
        <div className="mx-4 mb-2 shrink-0 rounded-2xl border border-alert-edge bg-alert-wash px-4 py-2.5 text-[0.85rem] leading-relaxed text-alert-ink sm:mx-6">
          {error}
        </div>
      )}

      <div className="mx-auto w-full max-w-[58rem] shrink-0 px-4 pb-3 pt-3 sm:px-7 sm:pb-5">
        {showDailyRecords && <DailyRecordEntry onOpen={onOpenAssessment} onHistory={onOpenAssessmentHistory ?? onOpenAssessment} refreshKey={assessmentRefreshKey} />}
        {failedSends.filter(item => item.sessionId === sessionId).map(item => <details key={item.id} open={item.userMessageId !== null} className="mb-3 rounded-2xl border border-alert-edge bg-alert-wash px-4 py-3 text-sm text-ink">
          <summary className="cursor-pointer font-medium">原文已保留 · {item.userMessageId === null ? "发送状态待核实" : "问题已收到，回复未完成"}</summary>
          <p className="mt-2 text-xs leading-6 text-ink-muted">{item.userMessageId === null
            ? "连接异常时可能已经送达。请先核对对话记录，再决定是否手动重试；不会自动重发。"
            : "本次回复未完成。重试前会核对记录；若仍无完整回复，将重新发送这条问题，已有内容会保留。"}</p>
          <p className="mt-2 max-h-32 overflow-y-auto whitespace-pre-wrap break-words text-sm leading-6" data-failed-send-text>{item.text}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {item.userMessageId !== null && onRetryFailedSend && <button type="button" disabled={busy || loading || recoveringSend}
              onClick={() => onRetryFailedSend(item)} className="min-h-10 rounded-xl bg-accent px-3 text-xs text-on-accent disabled:opacity-50">{recoveringSend ? "正在核对…" : "重新发送并生成回复"}</button>}
            {item.userMessageId === null && <button type="button" disabled={busy || loading || Boolean(input)}
              onClick={() => { setInput(current => restoreFailedText(current, item, sessionId)); composerRef.current?.focus(); }}
              className="min-h-10 rounded-xl border border-line px-3 text-xs disabled:opacity-50">放回输入框（不发送）</button>}
            <button type="button" onClick={() => onDismissFailedSend?.(item.id)} className="min-h-10 px-3 text-xs text-ink-muted">移除这份原文副本</button>
          </div>
          {item.userMessageId === null && input && <p className="mt-1 text-xs text-ink-muted">输入框已有内容，恢复原文不会覆盖它。</p>}
        </details>)}
        <form
          onSubmit={handleSubmit}
          className="composer-shell default-style:flex default-style:items-end default-style:gap-2 default-style:rounded-3xl default-style:py-2 default-style:pl-5 default-style:pr-2 default-style:transition-all default-style:duration-300 default-style:focus-within:ring-2 default-style:focus-within:ring-accent-edge simple:flex simple:items-end simple:gap-2 simple:rounded-2xl simple:py-2 simple:pl-5 simple:pr-2 simple:transition-all simple:duration-300 simple:focus-within:ring-2 simple:focus-within:ring-accent-edge"
        >
          <textarea
            ref={composerRef}
            value={input}
            onFocus={() => { setComposerFocused(true); setHeaderExpanded(false); setAccountMenuOpen(false); }}
            onBlur={() => setComposerFocused(false)}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) handleSubmit(e);
            }}
            placeholder={loading ? "正在加载对话…" : participant ? "输入消息" : "慢慢说…"}
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

        <div className="mt-1.5 flex items-center gap-2">
          <span className="w-8 shrink-0" aria-hidden="true" />
          <p className="flex-1 text-center text-[0.68rem] leading-relaxed text-ink-faint">
            内容由 AI 生成，仅供参考，不能替代专业建议。
          </p>
          <ContextUsageRing key={sessionId ?? "new"} sessionId={sessionId} busy={busy || loading}
            version={`${messages.length}:${messages.at(-1)?.id ?? ""}:${messages.at(-1)?.content.length ?? 0}`}
            draft={input} />
        </div>
      </div>
    </div>
  );
}
