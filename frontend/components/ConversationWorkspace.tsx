"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cancelGeneration, sameMessageTiming, streamChat, type ChatMessage, type RoutingMeta } from "@/lib/api";
import { hasDurableReplyForTurn } from "@/lib/replyBinding";
import {
  createConversation,
  deleteConversation,
  fetchConversation,
  fetchConversationRevision,
  getCurrentConversation,
  isMissing,
  listConversations,
  replyModeLabels,
  routingModeLabels,
  setConversationThinking,
  setConversationReplyEffort,
  subscribeConversation,
  updateConversation,
  type ConversationDetail,
  type ConversationSummary,
  type ConversationRoutingMode,
  type ConversationReplyMode,
  type ConversationReplyEffort,
} from "@/lib/conversations";
import Chat from "@/components/Chat";
import ConversationShareModal from "@/components/ConversationShareModal";
import ConversationModeModal from "@/components/ConversationModeModal";
import ConversationSidebar from "@/components/ConversationSidebar";
import ChangePasswordModal from "@/components/ChangePasswordModal";
import EmailSettingsModal from "@/components/EmailSettingsModal";
import ProfileModal from "@/components/ProfileModal";
import DailyAssessmentModal from "@/components/DailyAssessmentModal";
import PromptManagerModal from "@/components/PromptManagerModal";
import AdminAccountManagerModal from "@/components/AdminAccountManagerModal";
import IssueReportModal from "@/components/IssueReportModal";
import AdminIssueReportModal from "@/components/AdminIssueReportModal";
import AdminDailyRecords from "@/components/AdminDailyRecords";
import TestWorkbench from "@/components/TestWorkbench";
import GoalOverview from "@/components/GoalOverview";
import PushReminderModal from "@/components/PushReminderModal";
import { captureViewport } from "@/lib/capture";
import {
  startAdminSandbox,
  type SandboxModule,
} from "@/lib/adminSandbox";

/**
 * SSE is the primary live channel. These intervals govern reconnect attempts
 * and a tiny revision-only poll which protects mobile/ngrok sessions from a
 * proxy that leaves a dead streaming response hanging without an EOF.
 */
const SYNC_RECONNECT_MS = 1_000;
const SYNC_FALLBACK_INTERVAL_MS = 2_000;

type ConversationTurn = {
  id: string;
  sessionId: string;
  /** Assigned by the POST stream, never guessed from a local transcript. */
  userMessageId: number | null;
  startedAt: number;
  controller: AbortController;
  cancelled: boolean;
  stopRequested: boolean;
  messages: ChatMessage[];
  routing: Partial<RoutingMeta>;
  error: string | null;
  notice: string | null;
  replyWaiting: boolean;
  replyWaitPhase: "awaiting" | "waiting" | "closed";
};

/**
 * Mirrors the server's ORDER BY (pinned desc, updated_at desc). The list
 * arrives already sorted, so this exists for the optimistic path: toggling a
 * pin has to move the row immediately rather than waiting for a refetch.
 */
function sortConversations(list: ConversationSummary[]): ConversationSummary[] {
  return [...list].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  });
}

export default function ConversationWorkspace({
  displayName,
  accountUsername,
  displayIdentity,
  accountRole,
  accountEmail,
  accountEmailVerified,
  emailDeliveryAvailable,
  onAccountRefresh,
  onLogout,
}: {
  displayName: string;
  accountUsername: string;
  displayIdentity: string | null;
  accountRole: "user" | "admin";
  accountEmail: string | null;
  accountEmailVerified: boolean;
  emailDeliveryAvailable: boolean;
  onAccountRefresh: () => void | Promise<void>;
  onLogout: () => void;
}) {
  const isAdmin = accountRole === "admin";
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [conversationRoutingMode, setConversationRoutingMode] = useState<ConversationRoutingMode>("router_code");
  const [conversationReplyMode, setConversationReplyMode] = useState<ConversationReplyMode>("standard");
  const [replyWaiting, setReplyWaiting] = useState(false);
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [thinkingSaving, setThinkingSaving] = useState(false);
  const thinkingSaveRef = useRef(false);
  const [replyEffort, setReplyEffort] = useState<ConversationReplyEffort>("low");
  const [replyEffortOptions, setReplyEffortOptions] = useState<ConversationReplyEffort[] | null>(null);
  const [replyEffortSavingSessions, setReplyEffortSavingSessions] = useState<string[]>([]);
  const replyEffortSaveRef = useRef(new Set<string>());
  const replySettingsBySession = useRef(new Map<string, {
    revision: number; effort: ConversationReplyEffort; options: ConversationReplyEffort[] | null;
  }>());
  const replyEffortSaving = Boolean(sessionId && replyEffortSavingSessions.includes(sessionId));
  const [modeChooserOpen, setModeChooserOpen] = useState(false);
  const [modeChooserBusy, setModeChooserBusy] = useState(false);
  const [modeChooserError, setModeChooserError] = useState<string | null>(null);
  const activeSessionIdRef = useRef<string | null>(sessionId);
  // Navigation reads use a version; streams have their own per-room task.
  // Late navigation loads cannot replace a newer room, and a stream can
  // resume painting when the user returns to its room.
  const viewGenerationRef = useRef(0);
  const [routing, setRouting] = useState<Partial<RoutingMeta>>({});
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [generationNotice, setGenerationNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Starts true: held through the mount-time history check below so a
  // subject with a past conversation doesn't flash the scripted opening
  // message before it's replaced with their last chat.
  const [loadingConversation, setLoadingConversation] = useState(true);

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const [programRefreshKey, setProgramRefreshKey] = useState(0);
  const [goalsOpen, setGoalsOpen] = useState(false);
  const [pushSettingsOpen, setPushSettingsOpen] = useState(false);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("pa_push_check") === "1") {
      setPushSettingsOpen(true);
      url.searchParams.delete("pa_push_check");
      window.history.replaceState(window.history.state, "", url.toString());
    }
    if (url.searchParams.get("pa_reminder") === "1") {
      if (isAdmin) setGoalsOpen(true);
      else { setAssessmentView("record"); setAssessmentOpen(true); }
      url.searchParams.delete("pa_reminder");
      window.history.replaceState(window.history.state, "", url.toString());
    }
  }, []);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [desktopLayout, setDesktopLayout] = useState(true);
  useEffect(() => {
    const media = window.matchMedia("(min-width: 768px)");
    const update = () => { setDesktopLayout(media.matches); if (media.matches) setSidebarOpen(false); };
    update(); media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  // Opened only from the header button now — nothing checks assessment
  // status on mount, so chat is never blocked behind this.
  const [assessmentOpen, setAssessmentOpen] = useState(false);
  const [assessmentView, setAssessmentView] = useState<"record" | "history">("record");
  const appliedRevisions = useRef(new Map<string, number>());
  const pendingFloors = useRef(new Map<string, number>());
  const turns = useRef(new Map<string, ConversationTurn>());
  const mountedRef = useRef(true);
  useEffect(() => {
    // React Strict Mode replays this effect in development, so set the flag
    // in setup as well as clearing it in cleanup.
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      for (const turn of turns.current.values()) turn.controller.abort();
      turns.current.clear();
    };
  }, []);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [emailSettingsOpen, setEmailSettingsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [promptManagerOpen, setPromptManagerOpen] = useState(false);
  const [accountManagerOpen, setAccountManagerOpen] = useState(false);
  const [issueManagerOpen, setIssueManagerOpen] = useState(false);
  const [adminDailyOpen, setAdminDailyOpen] = useState(false);
  const [issueReportOpen, setIssueReportOpen] = useState(false);
  const [reportPreparing, setReportPreparing] = useState(false);
  const [reportScreenshot, setReportScreenshot] = useState<string | null>(null);
  const [reportCaptureError, setReportCaptureError] = useState<string | null>(null);
  const [sandboxStarting, setSandboxStarting] = useState(false);
  const [testWorkbenchOpen, setTestWorkbenchOpen] = useState(false);
  const [shareTarget, setShareTarget] = useState<{ sessionId: string; title: string } | null>(null);
  const creatingConversationRef = useRef<Promise<ConversationDetail> | null>(null);
  // The live-sync stream can observe a deletion just before the DELETE fetch
  // resolves. Mark locally initiated deletions so only one code path chooses
  // the next conversation (and therefore only one blank draft can be made).
  const deletingSessionIdsRef = useRef(new Set<string>());

  function publishTurn(turn: ConversationTurn) {
    if (activeSessionIdRef.current !== turn.sessionId || turns.current.get(turn.sessionId) !== turn) return;
    setMessages(turn.messages);
    setRouting(turn.routing);
    setError(turn.error);
    setGenerationNotice(turn.notice);
    setReplyWaiting(turn.replyWaiting);
    setStopping(turn.stopRequested);
    busyRef.current = true;
    setBusy(true);
  }

  const syncReplySettings = useCallback((detail: ConversationDetail) => {
    const known = replySettingsBySession.current.get(detail.session_id);
    const revision = detail.revision ?? 0;
    const settings = known && known.revision > revision ? known : {
      revision, effort: detail.reply_effort ?? "low", options: detail.reply_effort_options ?? null,
    };
    replySettingsBySession.current.set(detail.session_id, settings);
    if (activeSessionIdRef.current === detail.session_id) {
      setReplyEffort(settings.effort);
      setReplyEffortOptions(settings.options);
    }
  }, []);

  function showConversation(detail: ConversationDetail) {
    activeSessionIdRef.current = detail.session_id;
    setSessionId(detail.session_id);
    setConversationRoutingMode(detail.routing_mode ?? "router_code");
    setConversationReplyMode(detail.reply_mode ?? "standard");
    syncReplySettings(detail);
    if ((detail.revision ?? 0) >= (appliedRevisions.current.get(detail.session_id) ?? -1)) {
      setThinkingEnabled(detail.thinking_enabled ?? false);
    }
    const turn = turns.current.get(detail.session_id);
    if (turn) {
      publishTurn(turn);
      return;
    }
    busyRef.current = false;
    setBusy(false);
    setStopping(false);
    setGenerationNotice(null);
    setReplyWaiting(false);
    setMessages(detail.messages);
    setRouting(detail.next_module ? { next_module: detail.next_module, routing_pending: false, routed_by: "stored_state" } : {});
  }

  const applyRemoteSnapshot = useCallback((detail: ConversationDetail) => {
    if (!mountedRef.current) return;
    syncReplySettings(detail);
    setConversations((prev) =>
      sortConversations(
        prev.map((entry) =>
          entry.session_id === detail.session_id
            ? {
                ...entry,
                title: detail.title,
                updated_at: detail.updated_at,
                pinned: detail.pinned,
              }
            : entry,
        ),
      ),
    );

    // A subscription from the previous conversation can deliver one final
    // snapshot while React is switching views. Keep its sidebar metadata,
    // but never attach its module state or messages to the active chat.
    if (activeSessionIdRef.current !== detail.session_id) return;
    setConversationRoutingMode(detail.routing_mode ?? "router_code");
    setConversationReplyMode(detail.reply_mode ?? "standard");
    if ((detail.revision ?? 0) >= (appliedRevisions.current.get(detail.session_id) ?? -1)) {
      setThinkingEnabled(detail.thinking_enabled ?? false);
    }

    // Some mobile/proxy connections keep the POST body open after the server
    // has committed the assistant row and the client never receives the SSE
    // `persisted` frame. In that case the revision/event snapshot is the
    // authoritative completion signal. Adopt the durable transcript and
    // release this room's turn before aborting the orphaned stream. A snapshot
    // containing only the user row is deliberately ignored: generation is
    // still in flight and clearing it would permit duplicate submissions.
    const running = turns.current.get(detail.session_id);
    if (running) {
      if (!hasDurableReplyForTurn(detail, running)) return;
      running.messages = detail.messages;
      running.error = null;
      running.notice = null;
      running.replyWaiting = false;
      if (detail.next_module) {
        running.routing = {
          ...running.routing,
          next_module: detail.next_module,
          routing_pending: false,
          routed_by: "stored_state",
        };
      }
      turns.current.delete(detail.session_id);
      pendingFloors.current.delete(detail.session_id);
      running.controller.abort();
      setMessages(detail.messages);
      setRouting(running.routing);
      busyRef.current = false;
      setBusy(false);
      setStopping(false);
      setGenerationNotice(null);
      setReplyWaiting(false);
      void refreshConversations();
      return;
    }

    setRouting((prev) =>
      detail.next_module
        ? {
            ...prev,
            next_module: detail.next_module,
            routing_pending: false,
            routed_by: "stored_state",
          }
        : {},
    );

    // Never overwrite an assistant bubble while this device is appending
    // streamed deltas. The send completion path performs a full fetch.
    const revision = detail.revision ?? 0;
    if (revision < (appliedRevisions.current.get(detail.session_id) ?? -1)) return;
    const floor = pendingFloors.current.get(detail.session_id);
    if (floor !== undefined && detail.messages.length < floor) return;
    appliedRevisions.current.set(detail.session_id, revision);
    pendingFloors.current.delete(detail.session_id);
    setReplyWaiting(false);
    setMessages((prev) => {
      const same =
        prev.length === detail.messages.length &&
        prev.every(
          (message, index) =>
            message.role === detail.messages[index].role &&
            message.content === detail.messages[index].content &&
            message.created_at === detail.messages[index].created_at &&
            (message.reasoning_content ?? "") ===
              (detail.messages[index].reasoning_content ?? "") &&
            (message.model_name ?? "") ===
              (detail.messages[index].model_name ?? "") &&
            message.id === detail.messages[index].id &&
            message.reply_to_message_id === detail.messages[index].reply_to_message_id &&
            (message.routing_reasoning_content ?? "") ===
              (detail.messages[index].routing_reasoning_content ?? "") &&
            (message.router_model_name ?? "") ===
              (detail.messages[index].router_model_name ?? "") &&
            sameMessageTiming(message, detail.messages[index]),
        );
      return same ? prev : detail.messages;
    });

    // Defensive reconciliation for an earlier task that was removed by a
    // navigation/deletion race before its finally block could paint. Live
    // snapshots are only applied to the active room, so clearing stale busy
    // here cannot affect another conversation's in-flight task.
    if (busyRef.current) {
      busyRef.current = false;
      setBusy(false);
      setStopping(false);
      setGenerationNotice(null);
    }
  }, [syncReplySettings]);

  async function refreshConversations(): Promise<ConversationSummary[] | null> {
    try {
      const list = await listConversations();
      setConversations(list);
      return list;
    } catch {
      // The sidebar is a convenience over the chat, not a dependency of it —
      // a failed refresh just leaves the previous (possibly stale) list up
      // rather than surfacing an error banner over the conversation itself.
      return null;
    } finally {
      setConversationsLoading(false);
    }
  }

  function clearReplyWaiting() {
    const activeTurn = activeSessionIdRef.current ? turns.current.get(activeSessionIdRef.current) : undefined;
    if (activeTurn) {
      activeTurn.replyWaiting = false;
      activeTurn.replyWaitPhase = "closed";
    }
    setReplyWaiting(false);
  }

  async function beginConversation(mode?: ConversationRoutingMode, replyMode: ConversationReplyMode = "standard", effort?: ConversationReplyEffort): Promise<ConversationDetail | null> {
    if (isAdmin && mode === undefined) {
      setModeChooserError(null);
      setModeChooserOpen(true);
      setSidebarOpen(false);
      if (!activeSessionIdRef.current) setLoadingConversation(false);
      return null;
    }
    // The ref changes synchronously, so two clicks before React rerenders
    // cannot start two administrative POSTs with different mode selections.
    if (isAdmin && creatingConversationRef.current) return null;
    const generation = ++viewGenerationRef.current;
    clearReplyWaiting();
    activeSessionIdRef.current = null;
    setSessionId(null);
    setMessages([]);
    busyRef.current = false;
    setBusy(false);
    setStopping(false);
    setGenerationNotice(null);
    setLoadingConversation(true);
    setError(null);
    setSidebarOpen(false);
    try {
      // React development mode may replay mount effects. Share the same POST
      // while it is in flight so one visible draft cannot create two rows.
      const request =
        creatingConversationRef.current ??
        (creatingConversationRef.current = isAdmin ? createConversation(mode!, replyMode, effort) : getCurrentConversation());
      const detail = await request;
      if (generation !== viewGenerationRef.current) return null;
      showConversation(detail);
      setConversations((prev) =>
        sortConversations([
          {
            session_id: detail.session_id,
            title: detail.title,
            updated_at: detail.updated_at,
            pinned: detail.pinned,
          },
          ...prev.filter((item) => item.session_id !== detail.session_id),
        ]),
      );
      activeSessionIdRef.current = detail.session_id;
      return detail;
    } catch (err) {
      if (generation !== viewGenerationRef.current) return null;
      setMessages([]);
      setSessionId(null);
      setError(err instanceof Error ? err.message : String(err));
      return null;
    } finally {
      creatingConversationRef.current = null;
      if (generation === viewGenerationRef.current) {
        setLoadingConversation(false);
      }
    }
  }

  /**
   * A conversation the server no longer has. Drop it from the sidebar instead
   * of leaving a row that 404s every time it's touched.
   *
   * Without this the list only ever grew stale: it is loaded once on mount and
   * after each turn, so anything deleted elsewhere — another tab, another
   * device, a direct DB change — stayed on screen indefinitely, and every
   * click on it produced the same error again. Reconciling here is what makes
   * that self-healing rather than permanent.
   */
  function dropMissingConversation(targetSessionId: string) {
    setConversations((prev) =>
      prev.filter((c) => c.session_id !== targetSessionId),
    );
    if (deletingSessionIdsRef.current.has(targetSessionId)) return;
    const deletedTurn = turns.current.get(targetSessionId);
    turns.current.delete(targetSessionId);
    pendingFloors.current.delete(targetSessionId);
    deletedTurn?.controller.abort();
    if (targetSessionId === activeSessionIdRef.current) {
      clearReplyWaiting();
      setError("这条对话已经不存在了，已从列表中移除。");
      activeSessionIdRef.current = null;
      setMessages([]);
      setSessionId(null);
      setRouting({});
      // Prefer another existing conversation. A new blank conversation is
      // created only when the deleted one really was the final row.
      void (async () => {
        if (!isAdmin) {
          await beginConversation();
          return;
        }
        const remaining = await refreshConversations();
        if (remaining === null) {
          setError("对话已移除，但暂时无法刷新记录，请稍后重试。");
        } else if (remaining.length) {
          await loadConversation(remaining[0].session_id);
        } else {
          await beginConversation();
        }
      })();
    }
  }

  async function loadConversation(targetSessionId: string) {
    const generation = ++viewGenerationRef.current;
    clearReplyWaiting();
    activeSessionIdRef.current = targetSessionId;
    setSessionId(targetSessionId);
    const running = turns.current.get(targetSessionId);
    if (running) publishTurn(running);
    else {
      setMessages([]);
      setRouting({});
      busyRef.current = false;
      setBusy(false);
      setStopping(false);
      setGenerationNotice(null);
    }
    setLoadingConversation(true);
    setError(null);
    try {
      const detail = await fetchConversation(targetSessionId);
      if (generation !== viewGenerationRef.current) return;
      showConversation(detail);
    } catch (err) {
      if (generation !== viewGenerationRef.current) return;
      if (isMissing(err)) dropMissingConversation(targetSessionId);
      else setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (generation === viewGenerationRef.current) {
        setLoadingConversation(false);
      }
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!isAdmin) {
        await beginConversation();
        setConversationsLoading(false);
        return;
      }
      const list = await refreshConversations();
      if (cancelled) return;
      if (list === null) {
        setError("对话暂时加载失败，请刷新重试。已保存的对话会继续保留。");
        setLoadingConversation(false);
        return;
      }
      if (list.length > 0) {
        // Keep the administrator's existing pinned/recent list order.
        await loadConversation(list[0].session_id);
      } else {
        await beginConversation();
      }
    })();
    return () => { cancelled = true; };
  }, []);

  /** Cross-device live sync for the conversation currently on screen. */
  const busyRef = useRef(busy);
  busyRef.current = busy;

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    let controller: AbortController | null = null;

    async function connect() {
      while (!cancelled) {
        controller = new AbortController();
        try {
          await subscribeConversation(
            sessionId!,
            {
              onSnapshot: applyRemoteSnapshot,
              onDeleted: () => dropMissingConversation(sessionId!),
            },
            controller.signal,
          );
        } catch (err) {
          if (controller.signal.aborted || cancelled) return;
          // A network handover or proxy timeout is expected on a long-lived
          // mobile connection. Reconnect silently; the next snapshot is the
          // complete server state, so no event can be missed in the gap.
        }
        if (!cancelled) {
          await new Promise((resolve) => setTimeout(resolve, SYNC_RECONNECT_MS));
        }
      }
    }

    void connect();
    return () => {
      cancelled = true;
      controller?.abort();
    };
  }, [sessionId, applyRemoteSnapshot]);

  /**
   * A lightweight safety net for tunnels and mobile proxies which sometimes
   * stall a dead fetch stream forever instead of closing it. Only an integer
   * revision is polled; the full transcript is fetched when that integer
   * changes, so long conversations are not downloaded every two seconds.
   */
  const revisionRef = useRef<{ sessionId: string; revision: number } | null>(
    null,
  );

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    let inFlight = false;
    revisionRef.current = null;

    async function reconcile() {
      // Keep polling while a turn is streaming. The server may have already
      // committed the reply even when a mobile/proxy swallowed the final SSE
      // frame; applyRemoteSnapshot now distinguishes that durable reply from
      // an in-flight user-only snapshot and clears the local turn safely.
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        const current = await fetchConversationRevision(sessionId!);
        if (cancelled) return;
        const previous = revisionRef.current;
        if (
          previous?.sessionId === sessionId &&
          previous.revision === current.revision
        ) {
          return;
        }

        const detail = await fetchConversation(sessionId!);
        // Commit the observed revision only after the corresponding full
        // snapshot was fetched. If this second request fails transiently,
        // leaving the old revision forces the next poll to retry instead of
        // permanently skipping the durable assistant reply.
        if (!cancelled) {
          revisionRef.current = {
            sessionId: sessionId!,
            revision: current.revision,
          };
          applyRemoteSnapshot(detail);
        }
      } catch (err) {
        if (!cancelled && isMissing(err)) dropMissingConversation(sessionId!);
        // Transient network failures stay silent; the next interval retries.
      } finally {
        inFlight = false;
      }
    }

    void reconcile();
    const interval = window.setInterval(reconcile, SYNC_FALLBACK_INTERVAL_MS);
    const onFocus = () => void reconcile();
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [sessionId, applyRemoteSnapshot]);

  async function handleStop() {
    const sourceId = activeSessionIdRef.current;
    const turn = sourceId ? turns.current.get(sourceId) : undefined;
    if (!turn || turn.stopRequested) return;
    turn.stopRequested = true;
    turn.replyWaiting = false;
    turn.replyWaitPhase = "closed";
    turn.notice = "正在停止生成…";
    publishTurn(turn);
    try {
      const result = await cancelGeneration(turn.id);
      if (turns.current.get(turn.sessionId) !== turn) return;
      if (result.status === "cancelled") {
        turn.cancelled = true;
        turn.controller.abort();
      } else if (result.status === "finalizing" || result.status === "finished") {
        turn.stopRequested = false;
        turn.notice = "回复已完成，正在同步记录。";
        publishTurn(turn);
      }
    } catch {
      if (turns.current.get(turn.sessionId) !== turn) return;
      turn.stopRequested = false;
      turn.notice = null;
      turn.error = "停止请求未确认，请重试。当前回复可能仍在生成。";
      publishTurn(turn);
    }
  }

  async function handleThinkingToggle() {
    const target = activeSessionIdRef.current;
    if (!isAdmin || !target || loadingConversation || turns.current.has(target) || thinkingSaveRef.current || replyEffortSaveRef.current.has(target)) return;
    thinkingSaveRef.current = true;
    setThinkingSaving(true);
    try {
      const detail = await setConversationThinking(target, !thinkingEnabled);
      if (activeSessionIdRef.current === target) applyRemoteSnapshot(detail);
    } catch (err) {
      if (activeSessionIdRef.current === target) setError(err instanceof Error ? err.message : String(err));
    } finally {
      thinkingSaveRef.current = false;
      setThinkingSaving(false);
    }
  }

  async function handleReplyEffortChange(effort: ConversationReplyEffort) {
    const target = activeSessionIdRef.current;
    if (!isAdmin || !target || loadingConversation || conversationReplyMode !== "ack_deep"
      || !replyEffortOptions?.includes(effort) || effort === replyEffort || turns.current.has(target)
      || thinkingSaveRef.current || replyEffortSaveRef.current.has(target)) return;
    replyEffortSaveRef.current.add(target);
    setReplyEffortSavingSessions(previous => [...previous, target]);
    setError(null);
    try {
      const detail = await setConversationReplyEffort(target, effort);
      if (activeSessionIdRef.current === target) applyRemoteSnapshot(detail);
      else syncReplySettings(detail);
    } catch (err) {
      if (activeSessionIdRef.current === target) setError(err instanceof Error ? err.message : String(err));
    } finally {
      replyEffortSaveRef.current.delete(target);
      if (mountedRef.current) setReplyEffortSavingSessions(previous => previous.filter(id => id !== target));
    }
  }

  async function handleSend(text: string) {
    if (thinkingSaveRef.current) return;
    const sourceId = activeSessionIdRef.current;
    if (sourceId && replyEffortSaveRef.current.has(sourceId)) return;
    if (!sourceId || sourceId !== sessionId || loadingConversation || turns.current.has(sourceId)) return;
    const controller = new AbortController();
    const baseline = messages.length;
    const turn: ConversationTurn = {
      id: crypto.randomUUID(), sessionId: sourceId, userMessageId: null,
      startedAt: Date.now(), controller, cancelled: false, stopRequested: false,
      messages: [...messages, { role: "user", content: text, created_at: new Date().toISOString() }, {
        role: "assistant", content: "", reasoning_content: "", model_name: null,
        routing_reasoning_content: "", router_model_name: null,
      }],
      routing: { ...routing }, error: null, notice: null, replyWaiting: false, replyWaitPhase: "awaiting",
    };
    turns.current.set(sourceId, turn);
    pendingFloors.current.set(sourceId, baseline + 2);
    publishTurn(turn);
    let recovered = false;
    let recoveredDetail: ConversationDetail | null = null;
    let checking = false;
    const recoveryRequest: { current: AbortController | null } = { current: null };
    const ownsTask = () => turns.current.get(sourceId) === turn;
    const hasDurableReply = (detail: ConversationDetail) =>
      hasDurableReplyForTurn(detail, turn);
    const updateAssistant = (update: (message: ChatMessage) => ChatMessage) => {
      if (!ownsTask()) return;
      const next = [...turn.messages];
      const last = next[next.length - 1];
      if (last?.role !== "assistant") return;
      next[next.length - 1] = update(last);
      turn.messages = next;
      publishTurn(turn);
    };
    // Recovery belongs to the task, not the currently visible chat. A hidden
    // task can finish without touching another room's busy/error/stop state.
    const recovery = window.setInterval(async () => {
      if (!ownsTask() || checking || controller.signal.aborted || turn.stopRequested) return;
      checking = true;
      const request = new AbortController();
      recoveryRequest.current = request;
      const readTimeout = window.setTimeout(() => request.abort(), 8000);
      try {
        const detail = await fetchConversation(sourceId, request.signal);
        if (ownsTask() && !controller.signal.aborted && hasDurableReply(detail)) {
          recovered = true;
          recoveredDetail = detail;
          controller.abort();
        }
      } catch { /* The next bounded poll retries. */ }
      finally { window.clearTimeout(readTimeout); checking = false; }
    }, 5000);
    // The experiment's main request can itself use 180 seconds; leave room
    // for the unchanged risk, routing and retrieval stages ahead of it.
    const deadline = window.setTimeout(() => controller.abort(), conversationReplyMode === "ack_deep" ? 240000 : 180000);
    try {
      await streamChat(
        { message: text, session_id: sourceId, generation_id: turn.id },
        {
          onReplyWait: value => {
            if (!isAdmin || conversationReplyMode !== "ack_deep" || !ownsTask() || activeSessionIdRef.current !== sourceId
              || value.generation_id !== turn.id || turn.replyWaitPhase === "closed") return;
            if (value.waiting && !turn.messages.at(-1)?.content.trim()) return;
            turn.replyWaiting = value.waiting;
            turn.replyWaitPhase = value.waiting ? "waiting" : "closed";
            publishTurn(turn);
          },
          onDone: () => {
            if (!ownsTask()) return;
            turn.replyWaiting = false;
            turn.replyWaitPhase = "closed";
            publishTurn(turn);
          },
          onCancelled: () => {
            turn.cancelled = true;
            turn.replyWaiting = false;
            turn.replyWaitPhase = "closed";
          },
          onMeta: (meta) => {
            if (!ownsTask() || (meta.session_id && meta.session_id !== sourceId)) return;
            if (Number.isSafeInteger(meta.user_message_id) && meta.user_message_id! > 0) {
              turn.userMessageId = meta.user_message_id!;
            }
            if (meta.user_created_at) {
              turn.messages = turn.messages.map((message, index) => index === baseline
                ? { ...message, created_at: meta.user_created_at } : message);
            }
            if (meta.assistant_created_at) {
              updateAssistant(message => ({ ...message, created_at: meta.assistant_created_at }));
            }
            turn.routing = { ...turn.routing, ...meta };
            if (meta.model) updateAssistant(message => ({ ...message, model_name: meta.model }));
            else publishTurn(turn);
          },
          onDelta: delta => {
            if (delta.trim() && turn.replyWaitPhase === "waiting") {
              turn.replyWaiting = false;
              turn.replyWaitPhase = "closed";
            }
            updateAssistant(message => ({ ...message, content: message.content + delta }));
          },
          onReasoningDelta: delta => updateAssistant(message => ({
            ...message, reasoning_content: (message.reasoning_content ?? "") + delta,
          })),
          onRoutingReasoning: (text, model) => updateAssistant(message => ({
            ...message, routing_reasoning_content: text, router_model_name: model || null,
          })),
          onError: detail => {
            if (!ownsTask()) return;
            turn.replyWaiting = false;
            turn.replyWaitPhase = "closed";
            turn.error = detail;
            publishTurn(turn);
          },
        },
        controller.signal,
      );
    } catch (err) {
      if (!recovered && !turn.cancelled && ownsTask()) {
        turn.replyWaiting = false;
        turn.replyWaitPhase = "closed";
        turn.error = controller.signal.aborted
          ? "等待回复超时，已停止转圈并继续同步记录。请稍后查看，避免重复发送。"
          : err instanceof Error ? err.message : String(err);
        publishTurn(turn);
      }
    } finally {
      turn.replyWaiting = false;
      turn.replyWaitPhase = "closed";
      if (ownsTask() && activeSessionIdRef.current === sourceId) setReplyWaiting(false);
      window.clearInterval(recovery);
      window.clearTimeout(deadline);
      recoveryRequest.current?.abort();
      // Do not return early when ownership changed. A room switch or a late
      // deletion may remove the map entry while this promise is unwinding;
      // returning here used to leave the active UI's busy flag behind.
      if (ownsTask() && turn.cancelled) {
        pendingFloors.current.delete(sourceId);
        turn.error = null;
        turn.routing = { ...turn.routing, routing_pending: false };
        const partialReply = conversationReplyMode === "ack_deep" && Boolean(turn.messages.at(-1)?.content.trim());
        turn.notice = partialReply ? "已停止生成，已显示的内容会保留。你可以继续发送消息。" : "已停止生成，未完成的回复未保存。你可以继续发送消息。";
        if (turn.messages.at(-1)?.role === "assistant" && !partialReply) turn.messages = turn.messages.slice(0, -1);
      }
      let detail: ConversationDetail | null = recoveredDetail;
      if (!detail) {
        // A hidden/deleted task does not need another network read. The live
        // snapshot/revision path will reconcile it when the room is revisited.
        if (ownsTask()) {
          try { detail = await fetchConversation(sourceId, AbortSignal.timeout(10000)); }
          catch { /* Live sync retries, without changing a different chat. */ }
        }
      }
      // Recheck ownership AFTER awaiting: navigation or deletion can happen
      // during the final fetch, not just before it.
      if (ownsTask()) {
        if (detail && hasDurableReply(detail)) {
          turn.error = null;
          pendingFloors.current.delete(sourceId);
        }
        publishTurn(turn);
      }

      // Remove only this generation's entry. Never delete a newer turn that
      // could have been created after a navigation race.
      const sameTurn = turns.current.get(sourceId) === turn;
      if (sameTurn) {
        turns.current.delete(sourceId);
        pendingFloors.current.delete(sourceId);
      }
      // A completion from an older turn must not clear a newer turn that was
      // started in the same room after the durable snapshot path removed the
      // old entry. Only clear the room-level flag when no turn remains.
      if (mountedRef.current && activeSessionIdRef.current === sourceId && !turns.current.has(sourceId)) {
        busyRef.current = false;
        setBusy(false);
        setStopping(false);
        setReplyWaiting(false);
        if (!turn.cancelled) setGenerationNotice(null);
      }
      if (detail && activeSessionIdRef.current === sourceId) applyRemoteSnapshot(detail);
      if (sameTurn || detail) void refreshConversations();
    }
  }

  async function handleNew() {
    if (!isAdmin || loadingConversation) return;
    setSidebarOpen(false);
    await beginConversation();
  }

  async function handleModeChoice(mode: ConversationRoutingMode, replyMode: ConversationReplyMode, effort?: ConversationReplyEffort) {
    if (!isAdmin || creatingConversationRef.current) return;
    setModeChooserBusy(true);
    setModeChooserError(null);
    try {
      const detail = await beginConversation(mode, replyMode, effort);
      if (!mountedRef.current) return;
      if (detail) setModeChooserOpen(false);
      else setModeChooserError("新对话未能创建，请稍后重试。你的历史对话仍会保留。");
    } finally {
      if (mountedRef.current) setModeChooserBusy(false);
    }
  }

  async function handleSelect(targetSessionId: string) {
    setSidebarOpen(false);
    if (targetSessionId === activeSessionIdRef.current) return;
    await loadConversation(targetSessionId);
  }

  function handleShare(targetSessionId: string) {
    const summary = conversations.find((c) => c.session_id === targetSessionId);
    setSidebarOpen(false);
    setShareTarget({ sessionId: targetSessionId, title: summary?.title || "对话记录" });
  }

  /** Rename / pin. Optimistic, then reconciled against the server's row. */
  async function applyPatch(
    targetSessionId: string,
    patch: { title?: string; pinned?: boolean },
  ) {
    const previous = conversations;
    setConversations((prev) =>
      sortConversations(
        prev.map((c) =>
          c.session_id === targetSessionId ? { ...c, ...patch } : c,
        ),
      ),
    );
    try {
      const updated = await updateConversation(targetSessionId, patch);
      setConversations((prev) =>
        sortConversations(
          prev.map((c) => (c.session_id === targetSessionId ? updated : c)),
        ),
      );
    } catch (err) {
      if (isMissing(err)) {
        dropMissingConversation(targetSessionId);
        return;
      }
      // Put the old row back rather than leaving the sidebar showing a change
      // the server rejected.
      setConversations(previous);
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleDelete(targetSessionId: string) {
    // Optimistic: the sidebar is the source of truth for "did this work"
    // for the user, and a failed delete is rare enough that re-fetching to
    // roll back is simpler than holding the row hostage to the request.
    deletingSessionIdsRef.current.add(targetSessionId);
    setConversations((prev) =>
      prev.filter((c) => c.session_id !== targetSessionId),
    );
    try {
      await deleteConversation(targetSessionId);
      const deletedTurn = turns.current.get(targetSessionId);
      turns.current.delete(targetSessionId);
      pendingFloors.current.delete(targetSessionId);
      deletedTurn?.controller.abort();
      setProgramRefreshKey((value) => value + 1);
      if (activeSessionIdRef.current === targetSessionId) {
        activeSessionIdRef.current = null;
        setMessages([]);
        setSessionId(null);
        setRouting({});
        const remaining = await refreshConversations();
        if (remaining === null) {
          setError("对话已删除，但暂时无法刷新记录，请稍后重试。");
        } else if (remaining.length) {
          await loadConversation(remaining[0].session_id);
        } else {
          await beginConversation();
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      await refreshConversations();
    } finally {
      deletingSessionIdsRef.current.delete(targetSessionId);
    }
  }

  async function handleStartSandbox(module: SandboxModule) {
    if (busy || sandboxStarting) return;
    const generation = ++viewGenerationRef.current;
    setSandboxStarting(true);
    setLoadingConversation(true);
    setError(null);
    setSidebarOpen(false);
    try {
      const detail = await startAdminSandbox(module);
      if (generation !== viewGenerationRef.current) return;
      showConversation(detail);
      setRouting({
        next_module: detail.next_module,
        routing_pending: false,
        routed_by: "admin_sandbox",
      });
      setConversations((prev) =>
        sortConversations([
          {
            session_id: detail.session_id,
            title: detail.title,
            updated_at: detail.updated_at,
            pinned: detail.pinned,
          },
          ...prev.filter((item) => item.session_id !== detail.session_id),
        ]),
      );
    } catch (err) {
      if (generation !== viewGenerationRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSandboxStarting(false);
      if (generation === viewGenerationRef.current) setLoadingConversation(false);
    }
  }

  async function openIssueReport() {
    if (reportPreparing) return;
    // The report dialog must never be part of its own evidence. Unmount any
    // stale instance first, then give React and the browser two paint frames
    // to restore the unobstructed chat before html-to-image clones the page.
    setIssueReportOpen(false);
    setReportPreparing(true);
    setReportCaptureError(null);
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    try {
      await new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      );
      const timeout = new Promise<never>((_, reject) => {
        timeoutId = setTimeout(() => reject(new Error("capture timeout")), 7_000);
      });
      setReportScreenshot(await Promise.race([captureViewport(), timeout]));
    } catch {
      setReportScreenshot(null);
      setReportCaptureError("自动截图没有成功，你仍然可以只提交文字说明。");
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
      setReportPreparing(false);
      setIssueReportOpen(true);
    }
  }

  return (
    // Edge-to-edge shell with a narrow rail and independently capped text.
    <div className="zen-page-enter flex h-full w-full">
      {isAdmin && <ConversationSidebar
        conversations={conversations}
        activeSessionId={sessionId}
        loading={conversationsLoading}
        onSelect={handleSelect}
        onNew={handleNew}
        onDelete={handleDelete}
        onShare={handleShare}
        onRename={(id, title) => applyPatch(id, { title })}
        onTogglePin={(id, pinned) => applyPatch(id, { pinned })}
        onStartSandbox={
          accountRole === "admin" ? handleStartSandbox : undefined
        }
        sandboxBusy={busy || sandboxStarting}
        onOpenTestWorkbench={accountRole === "admin" ? () => setTestWorkbenchOpen(true) : undefined}
        onOpenAssessment={() => { setAssessmentView("record"); setAssessmentOpen(true); setSidebarOpen(false); }}
        onOpenGoals={() => { setGoalsOpen(true); setSidebarOpen(false); }}
        open={sidebarOpen}
        collapsed={sidebarCollapsed}
        onReportIssue={() => { setSidebarOpen(false); void openIssueReport(); }}
        reportPreparing={reportPreparing}
        onClose={() => setSidebarOpen(false)}
      />}

      <div className="flex h-full min-w-0 flex-1 flex-col" inert={isAdmin && sidebarOpen && !desktopLayout}>
        <div className="flex min-h-0 w-full flex-1">
        {/* `key` forces a full remount on conversation switch, so Chat's own
            local state (the draft input, composer height, scroll position)
            resets the same way a fresh page load would rather than carrying
            over from whatever was being typed in the previous conversation. */}
        {isAdmin && !sessionId && !loadingConversation ? <section className="workspace-card flex h-full w-full flex-col items-center justify-center px-6 text-center">
          <h1 className="text-lg font-semibold">开始一段新对话</h1>
          <p className="mt-3 max-w-sm text-sm leading-6 text-ink-muted">先选择这段对话的模块跳转方式和回复模式，再从模块一开始。目前还没有创建新对话。</p>
          {error && <p role="alert" className="mt-3 max-w-sm text-sm text-alert-ink">{error}</p>}
          <button type="button" onClick={handleNew} className="mt-6 min-h-11 rounded-xl bg-accent px-5 text-sm text-on-accent">选择对话模式</button>
          <div className="mt-3 flex gap-3"><button type="button" onClick={() => { if (desktopLayout) setSidebarCollapsed(false); else setSidebarOpen(true); }} className="min-h-11 px-2 text-sm text-ink-muted">查看对话历史</button><button type="button" onClick={onLogout} className="min-h-11 px-2 text-sm text-ink-muted">退出登录</button></div>
        </section> : <Chat
          key={sessionId ?? "draft"}
          sessionId={sessionId ?? undefined}
          messages={messages}
          routing={routing}
          headerContent={isAdmin ? <><h1 className="truncate text-[0.92rem] font-semibold tracking-[0.02em] text-ink">对话</h1>{sessionId && !loadingConversation && <div className="mt-0.5 flex flex-wrap gap-x-2 text-xs leading-4 text-accent-ink" title="本段对话创建时选定，不能在对话中切换"><p aria-label="当前对话模式">{routingModeLabels[conversationRoutingMode]}</p><p aria-label="当前回复模式">{replyModeLabels[conversationReplyMode]}</p></div>}</> : undefined}
          busy={busy}
          replyWaiting={replyWaiting}
          replyMode={conversationReplyMode}
          thinkingEnabled={thinkingEnabled}
          thinkingBusy={thinkingSaving}
          replyEffort={replyEffort}
          replyEffortOptions={replyEffortOptions ?? []}
          replyEffortBusy={replyEffortSaving}
          onReplyEffortChange={isAdmin && sessionId && conversationReplyMode === "ack_deep" && replyEffortOptions?.length ? handleReplyEffortChange : undefined}
          onToggleThinking={isAdmin && sessionId ? handleThinkingToggle : undefined}
          generationStartedAt={sessionId ? turns.current.get(sessionId)?.startedAt : undefined}
          loading={loadingConversation}
          error={error}
          onSend={handleSend}
          onStop={busy ? handleStop : undefined}
          stopping={stopping}
          generationNotice={generationNotice}
          onOpenSidebar={() => { if (window.matchMedia("(min-width: 768px)").matches) setSidebarCollapsed(value => !value); else setSidebarOpen(value => !value); }}
          onShareConversation={sessionId ? () => handleShare(sessionId) : undefined}
          sidebarExpanded={desktopLayout ? !sidebarCollapsed : sidebarOpen}
          onOpenAssessment={() => { setAssessmentView("record"); setAssessmentOpen(true); }}
          onOpenPushSettings={() => setPushSettingsOpen(true)}
          displayName={displayName}
          accountUsername={accountUsername}
          displayIdentity={displayIdentity}
          accountRole={accountRole}
          onLogout={onLogout}
          onChangePassword={() => setPasswordOpen(true)}
          onOpenEmailSettings={() => setEmailSettingsOpen(true)}
          onOpenProfile={() => setProfileOpen(true)}
          onOpenPromptManager={() => setPromptManagerOpen(true)}
          onOpenAccountManager={() => setAccountManagerOpen(true)}
          onOpenIssueManager={() => setIssueManagerOpen(true)}
          onOpenAdminDailyRecords={accountRole === "admin" ? () => setAdminDailyOpen(true) : undefined}
          onReportIssue={() => { void openIssueReport(); }}
          reportPreparing={reportPreparing}
        />}
        </div>
      </div>

      {isAdmin && modeChooserOpen && <ConversationModeModal busy={modeChooserBusy} error={modeChooserError} replyEffortOptions={replyEffortOptions}
        onChoose={handleModeChoice} onClose={() => { if (!creatingConversationRef.current) setModeChooserOpen(false); }} />}

      {isAdmin && <GoalOverview open={goalsOpen} sessionId={sessionId} busy={busy || loadingConversation}
        onOpenReminders={() => setPushSettingsOpen(true)}
        refreshKey={programRefreshKey} onClose={() => setGoalsOpen(false)} />}
      {pushSettingsOpen && <PushReminderModal onClose={() => setPushSettingsOpen(false)} />}
      {shareTarget && <ConversationShareModal
        key={shareTarget.sessionId}
        isAdmin={isAdmin}
        sessionId={shareTarget.sessionId}
        title={shareTarget.title}
        generationPending={turns.current.has(shareTarget.sessionId) || (shareTarget.sessionId === sessionId && (busy || !!routing.routing_pending))}
        onClose={() => setShareTarget(null)}
      />}

      {passwordOpen && (
        <ChangePasswordModal onClose={() => setPasswordOpen(false)} />
      )}

      {emailSettingsOpen && (
        <EmailSettingsModal
          currentEmail={accountEmail}
          verified={accountEmailVerified}
          deliveryAvailable={emailDeliveryAvailable}
          onSaved={onAccountRefresh}
          onClose={() => setEmailSettingsOpen(false)}
        />
      )}

      {profileOpen && (
        <ProfileModal
          onClose={() => setProfileOpen(false)}
          onSaved={onAccountRefresh}
        />
      )}

      {accountRole === "admin" && promptManagerOpen && (
        <PromptManagerModal onClose={() => setPromptManagerOpen(false)} />
      )}

      {accountRole === "admin" && accountManagerOpen && (
        <AdminAccountManagerModal
          currentUsername={accountUsername}
          onClose={() => setAccountManagerOpen(false)}
        />
      )}

      {accountRole === "admin" && issueManagerOpen && (
        <AdminIssueReportModal onClose={() => setIssueManagerOpen(false)} />
      )}
      {accountRole === "admin" && testWorkbenchOpen && <TestWorkbench onClose={() => setTestWorkbenchOpen(false)} />}
      {accountRole === "admin" && adminDailyOpen && <AdminDailyRecords onClose={() => setAdminDailyOpen(false)} />}

      {issueReportOpen && (
        <IssueReportModal
          initialScreenshot={reportScreenshot}
          initialCaptureError={reportCaptureError}
          sessionId={sessionId}
          lastError={error}
          onClose={() => {
            setIssueReportOpen(false);
            setReportScreenshot(null);
            setReportCaptureError(null);
          }}
        />
      )}

      {assessmentOpen && (
        <DailyAssessmentModal initialView={assessmentView} onClose={() => setAssessmentOpen(false)} />
      )}
    </div>
  );
}
