"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { streamChat, type ChatMessage, type RoutingMeta } from "@/lib/api";
import {
  createConversation,
  deleteConversation,
  fetchConversation,
  fetchConversationRevision,
  isMissing,
  listConversations,
  subscribeConversation,
  updateConversation,
  type ConversationDetail,
  type ConversationSummary,
} from "@/lib/conversations";
import { downloadMarkdown } from "@/lib/markdown";
import Chat from "@/components/Chat";
import ConversationSidebar from "@/components/ConversationSidebar";
import ChangePasswordModal from "@/components/ChangePasswordModal";
import EmailSettingsModal from "@/components/EmailSettingsModal";
import ProfileModal from "@/components/ProfileModal";
import DailyAssessmentModal from "@/components/DailyAssessmentModal";
import PromptManagerModal from "@/components/PromptManagerModal";
import AdminAccountManagerModal from "@/components/AdminAccountManagerModal";
import IssueReportModal from "@/components/IssueReportModal";
import AdminIssueReportModal from "@/components/AdminIssueReportModal";
import { captureViewport } from "@/lib/capture";
import { ReportMark } from "@/components/icons";
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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const activeSessionIdRef = useRef<string | null>(sessionId);
  activeSessionIdRef.current = sessionId;
  // Every navigation gets a generation. Async loads and chat streams may
  // finish later, but only work belonging to the current generation may
  // mutate the transcript on screen.
  const viewGenerationRef = useRef(0);
  const [routing, setRouting] = useState<Partial<RoutingMeta>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Starts true: held through the mount-time history check below so a
  // subject with a past conversation doesn't flash the scripted opening
  // message before it's replaced with their last chat.
  const [loadingConversation, setLoadingConversation] = useState(true);

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // Opened only from the header button now — nothing checks assessment
  // status on mount, so chat is never blocked behind this.
  const [assessmentOpen, setAssessmentOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [emailSettingsOpen, setEmailSettingsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [promptManagerOpen, setPromptManagerOpen] = useState(false);
  const [accountManagerOpen, setAccountManagerOpen] = useState(false);
  const [issueManagerOpen, setIssueManagerOpen] = useState(false);
  const [issueReportOpen, setIssueReportOpen] = useState(false);
  const [reportPreparing, setReportPreparing] = useState(false);
  const [reportScreenshot, setReportScreenshot] = useState<string | null>(null);
  const [reportCaptureError, setReportCaptureError] = useState<string | null>(null);
  const [sandboxStarting, setSandboxStarting] = useState(false);
  const creatingConversationRef = useRef<Promise<ConversationDetail> | null>(null);
  // The live-sync stream can observe a deletion just before the DELETE fetch
  // resolves. Mark locally initiated deletions so only one code path chooses
  // the next conversation (and therefore only one blank draft can be made).
  const deletingSessionIdsRef = useRef(new Set<string>());

  const applyRemoteSnapshot = useCallback((detail: ConversationDetail) => {
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
    if (busyRef.current) return;
    setMessages((prev) => {
      const same =
        prev.length === detail.messages.length &&
        prev.every(
          (message, index) =>
            message.role === detail.messages[index].role &&
            message.content === detail.messages[index].content &&
            (message.reasoning_content ?? "") ===
              (detail.messages[index].reasoning_content ?? "") &&
            (message.model_name ?? "") ===
              (detail.messages[index].model_name ?? "") &&
            (message.routing_reasoning_content ?? "") ===
              (detail.messages[index].routing_reasoning_content ?? "") &&
            (message.router_model_name ?? "") ===
              (detail.messages[index].router_model_name ?? ""),
        );
      return same ? prev : detail.messages;
    });
  }, []);

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

  async function beginConversation(): Promise<void> {
    const generation = ++viewGenerationRef.current;
    setLoadingConversation(true);
    setError(null);
    setSidebarOpen(false);
    try {
      // React development mode may replay mount effects. Share the same POST
      // while it is in flight so one visible draft cannot create two rows.
      const request =
        creatingConversationRef.current ??
        (creatingConversationRef.current = createConversation());
      const detail = await request;
      if (generation !== viewGenerationRef.current) return;
      setMessages(detail.messages);
      setSessionId(detail.session_id);
      setRouting({});
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
      setMessages([]);
      setSessionId(null);
      setError(err instanceof Error ? err.message : String(err));
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
    setError("这条对话已经不存在了，已从列表中移除。");
    if (targetSessionId === sessionId) {
      setMessages([]);
      setSessionId(null);
      setRouting({});
      // Prefer another existing conversation. A new blank conversation is
      // created only when the deleted one really was the final row.
      void (async () => {
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
    setLoadingConversation(true);
    setError(null);
    try {
      const detail = await fetchConversation(targetSessionId);
      if (generation !== viewGenerationRef.current) return;
      setMessages(detail.messages);
      setSessionId(detail.session_id);
      setRouting(
        detail.next_module
          ? {
              next_module: detail.next_module,
              routing_pending: false,
              routed_by: "stored_state",
            }
          : {},
      );
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
    (async () => {
      const list = await refreshConversations();
      if (list && list.length > 0) {
        // Most recently active conversation first (`listConversations` is
        // ordered by `updated_at desc`) — resume it instead of defaulting to
        // a blank draft, so a returning subject doesn't see the opening
        // message replayed on every page load. `loadConversation` clears
        // this same flag itself once it resolves.
        await loadConversation(list[0].session_id);
      } else {
        await beginConversation();
      }
    })();
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
      if (cancelled || inFlight || busyRef.current) return;
      inFlight = true;
      try {
        const current = await fetchConversationRevision(sessionId!);
        if (cancelled || busyRef.current) return;
        const previous = revisionRef.current;
        if (
          previous?.sessionId === sessionId &&
          previous.revision === current.revision
        ) {
          return;
        }

        revisionRef.current = {
          sessionId: sessionId!,
          revision: current.revision,
        };
        const detail = await fetchConversation(sessionId!);
        if (!cancelled && !busyRef.current) applyRemoteSnapshot(detail);
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

  async function handleSend(text: string) {
    const sendGeneration = viewGenerationRef.current;
    const ownsVisibleConversation = () =>
      sendGeneration === viewGenerationRef.current;
    setError(null);
    setBusy(true);
    busyRef.current = true;
    let resolvedSessionId = sessionId;
    // Push the user turn plus an empty assistant turn that deltas append to.
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text },
      {
        role: "assistant",
        content: "",
        reasoning_content: "",
        model_name: null,
        routing_reasoning_content: "",
        router_model_name: null,
      },
    ]);

    try {
      await streamChat(
        { message: text, session_id: sessionId },
        {
          onMeta: (meta) => {
            if (!ownsVisibleConversation()) return;
            // Meta arrives in two parts (session id first, routing decision
            // once the graph has made it) — merge rather than replace.
            if (meta.session_id) {
              resolvedSessionId = meta.session_id;
              setSessionId(meta.session_id);
            }
            setRouting((prev) => ({ ...prev, ...meta }));
            if (meta.model) {
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last?.role === "assistant") {
                  next[next.length - 1] = { ...last, model_name: meta.model };
                }
                return next;
              });
            }
          },
          onDelta: (delta) => {
            if (!ownsVisibleConversation()) return;
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              next[next.length - 1] = { ...last, content: last.content + delta };
              return next;
            });
          },
          onReasoningDelta: (delta) => {
            if (!ownsVisibleConversation()) return;
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              next[next.length - 1] = {
                ...last,
                reasoning_content: (last.reasoning_content ?? "") + delta,
              };
              return next;
            });
          },
          onRoutingReasoning: (text, model) => {
            if (!ownsVisibleConversation()) return;
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              next[next.length - 1] = {
                ...last,
                routing_reasoning_content: text,
                router_model_name: model || null,
              };
              return next;
            });
          },
          onError: (detail) => {
            if (ownsVisibleConversation()) setError(detail);
          },
        },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      busyRef.current = false;
      // Replace optimistic/local-only messages with the complete durable
      // transcript. This is the crucial equal-length case: another device's
      // two messages and this device's two local messages can have the same
      // count while being entirely different content.
      if (resolvedSessionId && ownsVisibleConversation()) {
        try {
          const detail = await fetchConversation(resolvedSessionId);
          applyRemoteSnapshot(detail);
        } catch {
          // The live stream will retry with a full snapshot. Chat completion
          // itself succeeded, so a transient sync read is not a user-facing
          // send failure.
        }
      }
      refreshConversations();
    }
  }

  async function handleNew() {
    // Reuse an untouched server-created draft instead of filling the sidebar
    // with duplicate "新对话" rows when the button is clicked repeatedly.
    if (
      sessionId &&
      messages.length === 1 &&
      messages[0].role === "assistant"
    ) {
      setSidebarOpen(false);
      return;
    }
    await beginConversation();
  }

  async function handleSelect(targetSessionId: string) {
    setSidebarOpen(false);
    if (targetSessionId === sessionId) return;
    await loadConversation(targetSessionId);
  }

  async function handleShare(targetSessionId: string) {
    // The sidebar only holds summaries; reuse the messages already in memory
    // for the open conversation rather than re-fetching them.
    const summary = conversations.find((c) => c.session_id === targetSessionId);
    const title = summary?.title || "对话记录";
    try {
      const detail =
        targetSessionId === sessionId
          ? { messages }
          : await fetchConversation(targetSessionId);
      downloadMarkdown(title, detail.messages);
    } catch (err) {
      if (isMissing(err)) dropMissingConversation(targetSessionId);
      else setError(err instanceof Error ? err.message : String(err));
    }
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
    const deletingCurrent = targetSessionId === sessionId;
    deletingSessionIdsRef.current.add(targetSessionId);
    setConversations((prev) =>
      prev.filter((c) => c.session_id !== targetSessionId),
    );
    try {
      await deleteConversation(targetSessionId);
      if (deletingCurrent) {
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
    setSandboxStarting(true);
    setLoadingConversation(true);
    setError(null);
    setSidebarOpen(false);
    try {
      const detail = await startAdminSandbox(module);
      setMessages(detail.messages);
      setSessionId(detail.session_id);
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
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSandboxStarting(false);
      setLoadingConversation(false);
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
    // No outer max-width: the sidebar should reach the actual left edge of
    // the viewport (modulo <main>'s own padding), not the left edge of some
    // artificially capped block floating in the middle of the screen. Only
    // the chat card itself is width-capped (inside the flex-1 wrapper below),
    // so it stays a comfortable reading width instead of stretching edge to
    // edge on a wide monitor — the sidebar reclaims the left margin, the chat
    // card's own max-width still bounds the right.
    <div className="zen-page-enter flex h-full w-full gap-4">
      <ConversationSidebar
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
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="flex h-full min-w-0 flex-1 justify-center">
        {/* `key` forces a full remount on conversation switch, so Chat's own
            local state (the draft input, composer height, scroll position)
            resets the same way a fresh page load would rather than carrying
            over from whatever was being typed in the previous conversation. */}
        <Chat
          key={sessionId ?? "draft"}
          messages={messages}
          routing={routing}
          busy={busy}
          loading={loadingConversation}
          error={error}
          onSend={handleSend}
          onOpenSidebar={() => setSidebarOpen(true)}
          onOpenAssessment={() => setAssessmentOpen(true)}
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
        />
      </div>

      {/* Keep support reachable without crowding the conversation header.
          It floats above the composer rather than over the send control; the
          whole control opts out of the automatic evidence screenshot. */}
      <button
        type="button"
        onClick={() => void openIssueReport()}
        disabled={reportPreparing}
        aria-label={reportPreparing ? "正在准备问题截图" : "我遇到问题"}
        title="我遇到问题"
        data-screenshot-exclude="true"
        className="fixed bottom-4 right-4 z-40 flex h-11 items-center gap-2 rounded-full border border-accent-edge bg-panel/90 px-3.5 text-xs font-medium text-accent-ink depth-composer backdrop-blur-xl transition-all duration-300 hover:-translate-y-0.5 hover:bg-raised disabled:cursor-wait disabled:opacity-60 disabled:hover:translate-y-0 sm:bottom-6 sm:right-6 sm:px-4"
      >
        {reportPreparing ? (
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-line-strong border-t-accent motion-reduce:animate-none" />
        ) : (
          <ReportMark className="h-4 w-4" />
        )}
        <span className="hidden sm:inline">我遇到问题</span>
      </button>

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
        <DailyAssessmentModal onClose={() => setAssessmentOpen(false)} />
      )}
    </div>
  );
}
