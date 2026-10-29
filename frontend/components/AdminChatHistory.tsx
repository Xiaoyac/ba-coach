"use client";

import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import MessageRow from "@/components/MessageRow";
import {
  ArrowLeftMark,
  CalendarMark,
  ChatBubbleMark,
  ChevronLeftMark,
  ChevronRightMark,
  CloseMark,
  SearchMark,
} from "@/components/icons";
import { fetchAccountConversations, type AdminAccountItem } from "@/lib/adminAccounts";
import type { ChatMessage } from "@/lib/api";
import type { ConversationDetail } from "@/lib/conversations";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

/** Local calendar day as "YYYY-MM-DD"; also used in element ids. */
function dayKey(value: string | Date): string {
  const date = new Date(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function dayLabel(value: string): string {
  const date = new Date(value);
  return date.toLocaleDateString("zh-CN", {
    year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
    month: "long", day: "numeric", weekday: "long",
  });
}

function stamp(value: string): string {
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

type Entry = { key: string; chat: ConversationDetail; message: ChatMessage; day: string | null };

/** A short excerpt around the first hit, with every hit marked. */
function excerpt(text: string, needle: string): ReactNode {
  // Drop markdown markers so an excerpt reads as the bubble does.
  const flat = text.replace(/\*\*|`|^#+\s|\|/gm, " ").replace(/^\s*-{3,}\s*$/gm, "").replace(/\s+/g, " ").trim();
  const at = flat.toLowerCase().indexOf(needle);
  const start = Math.max(0, at - 18);
  const slice = (start > 0 ? "…" : "") + flat.slice(start, start + 80) + (start + 80 < flat.length ? "…" : "");
  const parts = slice.split(new RegExp(`(${needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig"));
  return parts.map((part, i) => part.toLowerCase() === needle
    ? <mark key={i} className="rounded bg-accent-wash px-0.5 text-accent-ink">{part}</mark>
    : <Fragment key={i}>{part}</Fragment>);
}

/** Month grid in the WeChat manner: only days that have messages can be picked. */
function DatePicker({ days, initial, onPick }: { days: Set<string>; initial: string; onPick: (day: string) => void }) {
  const sorted = useMemo(() => [...days].sort(), [days]);
  const [month, setMonth] = useState(() => initial.slice(0, 7));
  const [year, monthIndex] = month.split("-").map(Number);
  const lead = (new Date(year, monthIndex - 1, 1).getDay() + 6) % 7;
  const length = new Date(year, monthIndex, 0).getDate();
  const first = sorted[0]?.slice(0, 7) ?? month;
  const last = sorted[sorted.length - 1]?.slice(0, 7) ?? month;
  function shift(delta: number) {
    const date = new Date(year, monthIndex - 1 + delta, 1);
    setMonth(dayKey(date).slice(0, 7));
  }
  return (
    <div className="w-[17.5rem] max-w-[calc(100vw-2rem)] rounded-2xl border border-line bg-panel p-4 depth-float">
      <div className="mb-3 flex items-center justify-between">
        <button type="button" onClick={() => shift(-1)} disabled={month <= first} aria-label="上个月"
          className="grid h-8 w-8 place-items-center rounded-full text-ink-muted hover:bg-raised disabled:opacity-30 disabled:hover:bg-transparent">
          <ChevronLeftMark className="h-4 w-4" />
        </button>
        <p className="text-sm font-medium text-ink">{year} 年 {monthIndex} 月</p>
        <button type="button" onClick={() => shift(1)} disabled={month >= last} aria-label="下个月"
          className="grid h-8 w-8 place-items-center rounded-full text-ink-muted hover:bg-raised disabled:opacity-30 disabled:hover:bg-transparent">
          <ChevronRightMark className="h-4 w-4" />
        </button>
      </div>
      <div className="grid grid-cols-7 gap-y-1 text-center">
        {WEEKDAYS.map((label) => <span key={label} className="pb-1 text-[0.66rem] text-ink-faint">{label}</span>)}
        {Array.from({ length: lead }, (_, i) => <span key={`lead-${i}`} />)}
        {Array.from({ length }, (_, i) => {
          const key = `${month}-${String(i + 1).padStart(2, "0")}`;
          const active = days.has(key);
          return (
            <button key={key} type="button" disabled={!active} onClick={() => onPick(key)}
              aria-label={active ? `跳到 ${monthIndex} 月 ${i + 1} 日` : undefined}
              className={`mx-auto grid h-8 w-8 place-items-center rounded-full text-[0.8rem] transition-colors duration-200 ${
                active ? "font-medium text-ink hover:bg-accent-wash hover:text-accent-ink" : "cursor-default text-ink-faint/50"
              } ${key === initial ? "bg-accent-wash text-accent-ink" : ""}`}>
              {i + 1}
            </button>
          );
        })}
      </div>
      <p className="mt-3 text-center text-[0.66rem] text-ink-faint">有聊天的日期可以点击</p>
    </div>
  );
}

/**
 * Read-only view of one account's whole chat history as a single timeline,
 * with WeChat-style keyword search and jump-to-date.
 */
export default function AdminChatHistory({
  account: initialAccount,
  onClose,
}: {
  account: AdminAccountItem;
  onClose: () => void;
}) {
  const [account, setAccount] = useState(initialAccount);
  const [chats, setChats] = useState<ConversationDetail[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [pickingDate, setPickingDate] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchAccountConversations(initialAccount.id, controller.signal)
      .then((result) => {
        setAccount(result.account);
        setChats(result.conversations);
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err));
      });
    return () => controller.abort();
  }, [initialAccount.id]);

  // Open at the newest message, the way the chat itself does.
  useLayoutEffect(() => {
    if (chats && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [chats]);

  const entries = useMemo<Entry[]>(() => (chats ?? []).flatMap((chat) =>
    chat.messages.map((message, index) => ({
      key: `${chat.session_id}-${message.id ?? index}`,
      chat,
      message,
      day: message.created_at ? dayKey(message.created_at) : null,
    }))), [chats]);
  const days = useMemo(() => new Set(entries.flatMap((entry) => entry.day ? [entry.day] : [])), [entries]);
  const needle = query.trim().toLowerCase();
  const hits = useMemo(() => needle
    ? entries.filter((entry) => entry.message.content.toLowerCase().includes(needle)).reverse()
    : [], [entries, needle]);
  const searching = needle.length > 0;

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      // Capture phase: the account manager underneath must not close as well.
      event.stopPropagation();
      if (pickingDate) setPickingDate(false);
      else if (searching) setQuery("");
      else onClose();
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [onClose, pickingDate, searching]);

  /** Scroll only the transcript; scrollIntoView would also move the sheet and hide its header. */
  function reveal(elementId: string, highlight?: string) {
    const box = scroller.current;
    const target = document.getElementById(elementId);
    if (!box || !target) return;
    const rect = target.getBoundingClientRect();
    // A day starts at the top; a search hit sits in the middle with its context around it.
    const inset = highlight ? Math.max(16, (box.clientHeight - rect.height) / 2) : 16;
    const top = rect.top - box.getBoundingClientRect().top + box.scrollTop - inset;
    box.scrollTo({ top, behavior: "smooth" });
    if (highlight) {
      setFlash(highlight);
      window.setTimeout(() => setFlash((current) => (current === highlight ? null : current)), 500);
    }
  }

  const name = account.nickname ?? account.username;
  const several = (chats?.length ?? 0) > 1;
  const newestDay = entries.length ? entries[entries.length - 1].day ?? dayKey(new Date()) : dayKey(new Date());
  let previousDay: string | null = null;

  return (
    <div className="zen-overlay-enter fixed inset-0 z-[60] flex items-center justify-center p-0 sm:p-4">
      <button
        type="button"
        aria-label="关闭聊天记录"
        onClick={onClose}
        className="absolute inset-0 bg-canvas/80 backdrop-blur-md"
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="chat-history-title"
        className="relative flex h-dvh w-full max-w-5xl flex-col overflow-hidden bg-panel depth-panel sm:h-[min(880px,calc(100dvh-2rem))] sm:rounded-[28px] sm:border sm:border-line"
      >
        <header className="shrink-0 border-b border-line px-4 pb-3 pt-3.5 sm:px-6">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-ink-muted transition-colors duration-300 hover:bg-raised hover:text-ink"
              aria-label="返回账号管理"
            >
              <ArrowLeftMark className="h-4 w-4" />
            </button>
            <span
              aria-hidden="true"
              className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-accent-wash text-sm font-medium text-accent-ink"
            >
              {Array.from(name)[0]?.toUpperCase()}
            </span>
            <div className="min-w-0 flex-1">
              <h2 id="chat-history-title" className="truncate text-[0.95rem] font-medium text-ink">
                {name} 的聊天记录
              </h2>
              <p className="mt-0.5 truncate text-[0.7rem] text-ink-faint">
                <span className="font-mono">@{account.username}</span>
                {account.research_id && <> · 被试编号 <span className="font-mono">{account.research_id}</span></>}
                {chats && <> · {entries.length} 条消息{several && <> · {chats.length} 段对话</>}</>}
              </p>
            </div>
            <span className="hidden shrink-0 rounded-full bg-raised px-2.5 py-1 text-[0.62rem] tracking-[0.12em] text-ink-muted sm:inline">
              只读
            </span>
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-ink-muted transition-colors duration-300 hover:bg-raised hover:text-ink"
            >
              <CloseMark className="h-4 w-4" />
            </button>
          </div>

          <div className="relative mt-3 flex items-center gap-2">
            <label className="flex min-w-0 flex-1 items-center gap-2 rounded-full border border-line bg-canvas/50 px-3.5 py-2 text-ink-faint">
              <SearchMark className="h-4 w-4 shrink-0" />
              <input
                value={query}
                onChange={(event) => { setQuery(event.target.value); setPickingDate(false); }}
                placeholder="搜索聊天内容"
                aria-label="搜索聊天内容"
                disabled={!entries.length}
                className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-ink-faint focus:outline-none"
              />
              {query && (
                <button type="button" onClick={() => setQuery("")} aria-label="清除搜索"
                  className="grid h-5 w-5 place-items-center rounded-full text-ink-muted hover:bg-raised">
                  <CloseMark className="h-3 w-3" />
                </button>
              )}
            </label>
            <button
              type="button"
              aria-expanded={pickingDate}
              disabled={!entries.length}
              onClick={() => setPickingDate((open) => !open)}
              className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3.5 py-2 text-sm transition-colors duration-200 disabled:opacity-40 ${
                pickingDate ? "border-accent-edge bg-accent-wash text-accent-ink" : "border-line text-ink-muted hover:bg-raised hover:text-ink"
              }`}
            >
              <CalendarMark className="h-4 w-4" />
              日期
            </button>
            {pickingDate && (
              <>
                <button type="button" aria-label="关闭日期选择" className="fixed inset-0 z-10 cursor-default" onClick={() => setPickingDate(false)} />
                <div className="absolute right-0 top-full z-20 mt-2">
                  <DatePicker
                    days={days}
                    initial={newestDay}
                    onPick={(day) => { setPickingDate(false); reveal(`admin-day-${day}`); }}
                  />
                </div>
              </>
            )}
          </div>
        </header>

        <div className="relative min-h-0 flex-1">
          <div ref={scroller} className="zen-scroll h-full overflow-y-auto px-3 py-6 sm:px-8">
            {error ? (
              <p role="alert" className="py-10 text-center text-sm text-alert-ink">{error}</p>
            ) : chats === null ? (
              <div className="mx-auto max-w-3xl space-y-5 py-2" aria-busy="true">
                {[0.55, 0.8, 0.4, 0.7].map((width, i) => (
                  <div key={i} className={`flex ${i % 2 === 0 ? "justify-end" : ""}`}>
                    <div className="h-12 animate-pulse rounded-2xl bg-raised/70" style={{ width: `${width * 100}%` }} />
                  </div>
                ))}
              </div>
            ) : entries.length === 0 ? (
              <div className="grid h-full place-items-center text-center text-ink-faint">
                <div>
                  <ChatBubbleMark className="mx-auto h-7 w-7 opacity-60" />
                  <p className="mt-3 text-xs">这个账号还没有聊过天</p>
                </div>
              </div>
            ) : (
              <div className="space-y-5 sm:space-y-6">
                <p className="pb-2 text-center text-[0.68rem] text-ink-faint">— 聊天记录从这里开始 —</p>
                {chats.map((chat) => (
                  <section key={chat.session_id} aria-label={chat.title || "未命名对话"} className="space-y-5 sm:space-y-6">
                    {several && chat.messages.length > 0 && (
                      <div className="mx-auto max-w-3xl pt-4 text-center">
                        <span className="inline-block max-w-full truncate rounded-full bg-raised px-3.5 py-1.5 text-[0.7rem] text-ink-muted">
                          新对话 · {chat.title || "未命名对话"}
                        </span>
                      </div>
                    )}
                    {chat.messages.map((message, index) => {
                      const key = `${chat.session_id}-${message.id ?? index}`;
                      const day = message.created_at ? dayKey(message.created_at) : null;
                      const newDay = day !== null && day !== previousDay;
                      if (day !== null) previousDay = day;
                      return (
                        <Fragment key={key}>
                          {newDay && (
                            <div id={`admin-day-${day}`} role="separator"
                              className="mx-auto flex max-w-3xl items-center gap-3 pt-2 text-[0.68rem] text-ink-faint">
                              <span className="h-px flex-1 bg-line" />
                              {dayLabel(message.created_at!)}
                              <span className="h-px flex-1 bg-line" />
                            </div>
                          )}
                          <div id={`admin-msg-${key}`}
                            className={`-mx-3 px-3 py-1 transition-colors duration-200 sm:-mx-8 sm:px-8 ${flash === key ? "bg-[color-mix(in_srgb,var(--tint)_4%,transparent)]" : "bg-transparent"}`}>
                            <MessageRow
                              message={message}
                              pending={false}
                              routingPending={false}
                              animate={false}
                              showDiagnostics={false}
                              knowledgeSource={{ kind: "snapshot", data: null }}
                            />
                          </div>
                        </Fragment>
                      );
                    })}
                  </section>
                ))}
                <p className="pt-6 text-center text-[0.68rem] text-ink-faint">
                  — 以上是全部记录 · 只读查看，不会通知用户，也不会改动对话 —
                </p>
              </div>
            )}
          </div>

          {searching && (
            <div className="zen-scroll absolute inset-0 overflow-y-auto bg-panel px-3 py-4 sm:px-8" aria-label="搜索结果">
              <p className="mx-auto max-w-3xl px-2 pb-3 text-[0.7rem] text-ink-faint">
                {hits.length ? `找到 ${hits.length} 条相关消息，点击跳到原文` : "没有找到相关消息"}
              </p>
              <ul className="mx-auto max-w-3xl space-y-1">
                {hits.map((hit) => (
                  <li key={hit.key}>
                    <button
                      type="button"
                      onClick={() => { setQuery(""); requestAnimationFrame(() => reveal(`admin-msg-${hit.key}`, hit.key)); }}
                      className="flex w-full items-start gap-3 rounded-2xl px-3 py-3 text-left transition-colors duration-200 hover:bg-raised"
                    >
                      <span className={`mt-0.5 shrink-0 rounded-full px-2 py-0.5 text-[0.62rem] ${
                        hit.message.role === "user" ? "bg-accent-wash text-accent-ink" : "bg-raised text-ink-muted"
                      }`}>
                        {hit.message.role === "user" ? name : "教练"}
                      </span>
                      <span className="min-w-0 flex-1 text-[0.84rem] leading-6 text-ink">{excerpt(hit.message.content, needle)}</span>
                      {hit.message.created_at && (
                        <span className="shrink-0 pt-0.5 text-[0.66rem] text-ink-faint">{stamp(hit.message.created_at)}</span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
