"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  fetchAssessmentHistory,
  submitAssessment,
  type ActivityLog,
  type AssessmentRecord,
  type DailySummary,
} from "@/lib/assessment";
import AssessmentHistory from "@/components/AssessmentHistory";
import {
  ArrowLeftMark,
  ChevronDownMark,
  CloseMark,
  HistoryMark,
  PlusMark,
} from "@/components/icons";

/* -------------------------------------------------------------------------
 * Question definitions.
 *
 * Kept as data rather than repeated JSX so the wording, the state key and the
 * scale can never drift apart — every rendered control is generated from the
 * same row that names the field it writes.
 * ---------------------------------------------------------------------- */

type ActivityField = Exclude<keyof ActivityLog, "time_slot" | "activity" | "note">;

const ACTIVITY_SCALES: { key: ActivityField; label: string; hint: string }[] = [
  { key: "achievement", label: "成就", hint: "做成了什么" },
  { key: "connection", label: "联结", hint: "与人的靠近" },
  { key: "enjoyment", label: "愉悦", hint: "享受的程度" },
  { key: "importance", label: "重要", hint: "对你的意义" },
];

type SummaryField = "completion_rate" | "activity_level" | "overall_mood";

const SUMMARY_SCALES: {
  key: SummaryField;
  label: string;
  low: string;
  high: string;
}[] = [
  { key: "completion_rate", label: "想做的事情完成程度", low: "几乎没完成", high: "都完成了" },
  { key: "activity_level", label: "今天总体身体活动程度", low: "几乎没有活动", high: "活动很多" },
  { key: "overall_mood", label: "回顾今天，你今天整体心情如何？", low: "很低落", high: "很愉快" },
];

const emptyActivity = (): ActivityLog => ({
  time_slot: "",
  activity: "",
  emotion: null,
  achievement: null,
  connection: null,
  enjoyment: null,
  importance: null,
  note: "",
});

const defaultSummary = (): DailySummary => ({
  completion_rate: null,
  completion_not_applicable: false,
  activity_level: null,
  overall_mood: null,
  reflection_note: "",
});

// One visual grammar for required fields, even when their input types differ.
const FIELD_LABEL = "flex items-center gap-2 text-sm font-medium leading-5 text-ink";
const FIELD_CONTROL = "rounded-xl border bg-sheet text-sm text-ink transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

export default function DailyAssessmentModal({
  onClose,
  initialView = "record",
}: {
  /** Fired whenever the window goes away — submitted, skipped, the X, or
   *  clicking outside it. This is a manually opened tool now, not a gate
   *  the rest of the app waits on, so the caller only ever needs "it's
   *  closed" and doesn't have to tell those cases apart. */
  onClose: () => void;
  initialView?: "record" | "history";
}) {
  const [view, setView] = useState<"record" | "history">(initialView);
  useEffect(() => { if (initialView === "history") void loadHistory(0, false); }, [initialView]);
  const [activities, setActivities] = useState<ActivityLog[]>([emptyActivity()]);
  const [summary, setSummary] = useState<DailySummary>(defaultSummary);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<AssessmentRecord[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyNextOffset, setHistoryNextOffset] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const submittingRef = useRef(false);
  const titleId = useId();

  // Native modal semantics keep focus in the form and restore it on close.
  useEffect(() => { dialogRef.current?.showModal(); }, []);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [view]);

  const filled = activities.filter((a) => a.activity.trim() || a.time_slot || a.emotion !== null || a.note?.trim() || ACTIVITY_SCALES.some(s => a[s.key] !== null));
  const missingTimeSlot = filled.some((a) => !validTimeRange(a.time_slot));
  const missingRequired = filled.some(a => !a.activity.trim() || a.emotion === null);
  const missingSummary = summary.activity_level === null || summary.overall_mood === null || summary.completion_rate === null;
  const canSubmit = !missingTimeSlot && !missingRequired && !missingSummary;

  function patchActivity(index: number, patch: Partial<ActivityLog>) {
    setActivities((prev) =>
      prev.map((a, i) => (i === index ? { ...a, ...patch } : a)),
    );
  }

  function addActivity() {
    setActivities((prev) => [...prev, emptyActivity()]);
  }

  function removeActivity(index: number) {
    // Keep one editable activity even after the last row is removed.
    setActivities((prev) =>
      prev.length === 1 ? [emptyActivity()] : prev.filter((_, i) => i !== index),
    );
  }

  async function loadHistory(offset: number, append: boolean) {
    if (historyLoading || historyLoadingMore) return;
    append ? setHistoryLoadingMore(true) : setHistoryLoading(true);
    setHistoryError(null);
    try {
      const page = await fetchAssessmentHistory(offset);
      setHistory((prev) => (append ? [...prev, ...page.items] : page.items));
      setHistoryHasMore(page.has_more);
      setHistoryNextOffset(page.next_offset);
      setHistoryLoaded(true);
    } catch (err) {
      setHistoryError(
        err instanceof Error ? "暂时没能读取历史记录，请稍后再试。" : String(err),
      );
    } finally {
      append ? setHistoryLoadingMore(false) : setHistoryLoading(false);
    }
  }

  function showHistory() {
    setView("history");
    if (!historyLoaded && !historyLoading) void loadHistory(0, false);
  }

  async function handleSubmit() {
    if (!canSubmit || submittingRef.current) return;
    submittingRef.current = true;
    setBusy(true);
    setError(null);
    try {
      await submitAssessment({
        // Blank cards are dropped rather than rejected: someone who tapped
        // "add" and changed their mind should not be sent back to fix it.
        activities: filled.map((a) => ({
          ...a,
          activity: a.activity.trim(),
          time_slot: a.time_slot.trim(),
          note: a.note?.trim() ? a.note.trim() : null,
        })),
        summary: {
          ...summary,
          // New entries always rate completion; historical N/A remains readable.
          completion_not_applicable: false,
          reflection_note: summary.reflection_note?.trim()
            ? summary.reflection_note.trim()
            : null,
        },
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      submittingRef.current = false;
      setBusy(false);
    }
  }

  /** Closing mid-submit would leave `busy` racing the unmount; simplest to
   *  just not let a stray outside click or Escape interrupt an in-flight
   *  request. */
  function handleDismiss() {
    if (busy) return;
    onClose();
  }

  return (
    <dialog
      ref={dialogRef}
      className="m-auto max-h-[calc(100dvh-40px)] w-[min(1040px,calc(100vw-40px))] max-w-none overflow-hidden rounded-3xl border-0 bg-sheet p-0 text-ink shadow-2xl backdrop:bg-black/40 max-sm:h-[100dvh] max-sm:max-h-[100dvh] max-sm:w-screen max-sm:rounded-none"
      aria-labelledby={titleId}
      onCancel={(event) => { if (busy) event.preventDefault(); }}
      onClose={handleDismiss}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) handleDismiss();
      }}
    >
      <form
        onSubmit={(event) => { event.preventDefault(); void handleSubmit(); }}
        aria-busy={busy}
        className="flex max-h-[calc(100dvh-40px)] min-h-0 flex-col max-sm:h-full max-sm:max-h-[100dvh]"
      >
        {/* ---- header ---- */}
        <header className="shrink-0 px-5 pb-4 pt-5 sm:px-7">
          <div className="flex items-center gap-3">
            <div className="min-w-0">
              <h2
                id={titleId}
                className="truncate text-[1.05rem] font-medium tracking-wide text-ink"
              >
                {view === "history" ? "历史每日记录" : "今天的行为记录"}
              </h2>
              <p className="mt-0.5 truncate text-xs leading-relaxed text-ink-faint">
                {view === "history"
                  ? "看看过去的行动，也看看自己走过的路"
                  : "记下一点行动，也照顾一下今天的感受。"}
              </p>
            </div>
            <button
              type="button"
              onClick={handleDismiss}
              disabled={busy}
              aria-label="关闭"
              className="ml-auto flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-ink-muted transition-colors hover:bg-raised hover:text-ink disabled:opacity-40"
            >
              <CloseMark className="h-3.5 w-3.5" />
            </button>
          </div>

        </header>

        {/* ---- body: the only scrolling region ---- */}
        <div
          ref={scrollRef}
          data-daily-scroll
          className="zen-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-5 sm:px-7"
        >
          {view === "history" ? (
            <AssessmentHistory
              records={history}
              loading={historyLoading}
              loadingMore={historyLoadingMore}
              error={historyError}
              hasMore={historyHasMore}
              onLoadMore={() => {
                if (historyNextOffset !== null) {
                  void loadHistory(historyNextOffset, true);
                }
              }}
              onRetry={() => void loadHistory(0, false)}
            />
          ) : (
            <fieldset disabled={busy} className="grid min-w-0 items-start gap-6 lg:grid-cols-2 lg:gap-6">
            <section aria-labelledby={`${titleId}-activities`} className="min-w-0 space-y-3">
              <div className="flex items-baseline justify-between gap-3"><h3 id={`${titleId}-activities`} className="text-base font-semibold">今天做了什么</h3><span className="text-xs text-ink-muted">按活动记录（可选）</span></div>
              {activities.map((a, i) => (
                <ActivityCard
                  key={i}
                  index={i}
                  value={a}
                  onChange={(patch) => patchActivity(i, patch)}
                  onRemove={() => removeActivity(i)}
                  removable
                />
              ))}

              <button
                type="button"
                onClick={addActivity}
                className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-accent-wash px-4 py-2 text-sm font-medium text-accent-ink transition-colors hover:bg-raised"
              >
                <PlusMark className="h-4 w-4" />
                添加一项活动
              </button>
            </section>
            <section aria-labelledby={`${titleId}-summary`} className="min-w-0 space-y-3">
              <div className="flex items-baseline justify-between gap-3"><h3 id={`${titleId}-summary`} className="text-base font-semibold">回看这一天</h3><span className="text-xs text-ink-muted">按天记录</span></div>
              <div data-daily-summary-panel className="space-y-5 rounded-2xl bg-raised p-4 sm:p-5">
              <div className="flex min-h-7 items-center justify-between gap-3"><h4 className="text-sm font-semibold">整体感受</h4><span className="text-xs text-ink-muted">3 项评分 · 0–5 分</span></div>
              {SUMMARY_SCALES.map((s) => (
                <div key={s.key}>
                  <SegmentedScore
                    label={s.label} hint={`0 · ${s.low}　—　5 · ${s.high}`} value={summary[s.key]} required
                    onChange={v => setSummary(prev => ({ ...prev, [s.key]: v }))}
                  />
                </div>
              ))}

              <label className="block">
                <span className="mb-2 block text-[0.85rem] text-ink-muted">
                  想再写一点（可留空）
                </span>
                <textarea
                  value={summary.reflection_note ?? ""}
                  onChange={(e) =>
                    setSummary((prev) => ({
                      ...prev,
                      reflection_note: e.target.value,
                    }))
                  }
                  rows={2}
                  placeholder="今天有什么想记下来的…"
                  className={`zen-scroll block min-h-16 w-full resize-y border-line px-3 py-2 leading-6 placeholder:text-ink-muted ${FIELD_CONTROL}`}
                />
              </label>
              </div>
            </section>
            </fieldset>
          )}
        </div>

        {/* ---- footer ---- */}
        <div className="shrink-0 bg-raised/50 px-5 py-3 sm:px-7">
          {view === "record" && error && (
            <p role="alert" className="mb-3 rounded-2xl bg-alert-wash px-4 py-2.5 text-sm leading-relaxed text-alert-ink">
              {error}
            </p>
          )}

          {view === "history" ? (
            <button
              type="button"
              onClick={() => setView("record")}
              className="flex items-center gap-2 rounded-full border border-line px-4 py-2 text-[0.82rem] text-ink-muted transition-colors duration-300 hover:border-accent-edge hover:text-accent-ink"
            >
              <ArrowLeftMark className="h-3.5 w-3.5" />
              返回今日记录
            </button>
          ) : (
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 sm:flex-nowrap">
            <button
              type="button"
              onClick={showHistory}
              disabled={busy}
              className="flex shrink-0 items-center gap-2 rounded-full px-2 py-2 text-[0.8rem] text-ink-faint transition-colors duration-300 hover:bg-accent-wash hover:text-accent-ink disabled:opacity-40 sm:px-3"
            >
              <HistoryMark className="h-4 w-4" />
              查看历史
            </button>

            <p id={`${titleId}-validation`} className="order-first basis-full text-xs leading-relaxed text-ink-muted sm:order-none sm:max-w-[45%] sm:basis-auto">{missingRequired || missingTimeSlot ? "请补全已填写活动的时间、内容和做完后的心情，或删除该活动" : missingSummary ? "请完成今日三项总体评分" : filled.length === 0 ? "可仅保存今日整体总结" : `已填写 ${filled.length} 项活动`}</p>
              <button
                type="submit"
                disabled={busy || !canSubmit}
                aria-describedby={`${titleId}-validation`}
                className="flex min-h-11 shrink-0 items-center gap-2 rounded-full bg-accent px-5 py-2 text-sm font-medium text-on-accent transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy && (
                  <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line-strong border-t-accent motion-reduce:animate-none" />
                )}
                {busy ? "正在保存…" : "保存今日记录"}
              </button>
          </div>
          )}
        </div>
      </form>
    </dialog>
  );
}

/* ---------------------------------------------------------------------- */

function ActivityCard({
  index,
  value,
  onChange,
  onRemove,
  removable,
}: {
  index: number;
  value: ActivityLog;
  onChange: (patch: Partial<ActivityLog>) => void;
  onRemove: () => void;
  removable: boolean;
}) {
  // Flagged once the card actually has content — an untouched fresh card
  // (both fields empty) is dropped silently on submit, not an error to nag
  // about the moment it appears.
  const timeSlotMissing =
    value.activity.trim().length > 0 && !validTimeRange(value.time_slot);
  const contentId = useId();

  return (
    <section aria-label={`活动 ${index + 1}`} className="rounded-2xl bg-raised p-4 sm:p-5">
      <div className="mb-3 flex min-h-7 items-center gap-3">
        <h4 className="text-sm font-semibold">活动 {index + 1}</h4>
        <span className="ml-auto text-xs text-ink-muted">记录活动时需填写下面 3 项</span>
        {removable && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={`删除活动 ${index + 1}`}
            className="-my-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-ink-muted transition-colors hover:bg-alert-wash hover:text-alert-ink"
          >
            <CloseMark className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <div data-activity-required className="space-y-4">
      <div className="space-y-2">
        <p className={FIELD_LABEL}>活动时间 <RequiredMark /></p>
        <TimeSlotSelect
          value={value.time_slot}
          onChange={(time_slot) => onChange({ time_slot })}
          invalid={timeSlotMissing}
          label={`活动 ${index + 1} 的时间段`}
        />
        {timeSlotMissing && <p className="text-xs text-alert-ink">请选完整的时间段，结束时间须晚于开始。</p>}
      </div>

      <div className="space-y-2">
      <label htmlFor={contentId} className={FIELD_LABEL}>
        活动内容 <RequiredMark />
      </label>
      <textarea
        id={contentId}
        rows={2}
        value={value.activity}
        aria-required="true"
        onChange={(e) => onChange({ activity: e.target.value })}
        // Concrete examples, not just "做了什么…": a blank prompt invites
        // abstractions ("休息了一下"), and the whole point of the record is
        // one nameable activity per row.
        placeholder="例如：晚饭后散步十分钟"
        aria-label={`活动 ${index + 1} 的内容`}
        className={`zen-scroll block min-h-16 w-full resize-y border-line px-3 py-2 leading-6 placeholder:text-ink-muted ${FIELD_CONTROL}`}
      />
      </div>

      <div data-activity-mood>
        <SegmentedScore label="做完活动后的心情" hint="0 · 很低落　—　5 · 很愉快" required value={value.emotion} onChange={emotion => onChange({emotion})} />
      </div>
      </div>
      <section aria-label="其他感受（可选）" className="mt-4 rounded-xl bg-sheet/50 p-3">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1"><h4 className="text-sm font-medium text-ink-muted">其他感受</h4><p className="text-xs text-ink-muted">4项选填</p></div>
        <div className="grid gap-x-4 gap-y-2.5 sm:grid-cols-2">
        {ACTIVITY_SCALES.map((s) => (
          <SegmentedScore
            key={s.key}
            label={s.label}
            hint={s.hint}
            compact
            value={value[s.key]}
            onChange={(v) => onChange({ [s.key]: value[s.key] === v ? null : v } as Partial<ActivityLog>)}
          />
        ))}
        </div>
        <p className="mt-2 text-xs leading-5 text-ink-muted">0 · 很少 — 5 · 很多；再点一次可取消。</p>
      </section>

      <input
        value={value.note ?? ""}
        onChange={(e) => onChange({ note: e.target.value })}
        placeholder="一句备注（可留空）"
        aria-label={`活动 ${index + 1} 的备注`}
        className="mt-3 min-h-11 w-full rounded-xl border border-line bg-transparent px-3 py-2 text-sm text-ink placeholder:text-ink-muted transition-colors focus-visible:outline-2 focus-visible:outline-accent"
      />
    </section>
  );
}
/**
 * An hour-range picker: "从 19:00 到 20:00".
 *
 * Replaces a six-preset dropdown. The presets were a reasonable guess at how
 * people recall a day, but they cannot express "I walked from 7 to 8", and
 * `activity_logs.time_slot` is a VARCHAR — the column never needed them.
 *
 * Hours only, no minutes. This is a recalled activity, not a calendar entry;
 * asking for 19:35 invites a precision nobody actually has and makes the
 * control fiddlier for no gain.
 *
 * Built as real DOM rather than two `<select>`s because a native select's
 * open list is painted by the OS, not the page — border-radius, background
 * and hover colour never reach it. That trade costs keyboard support and the
 * listbox ARIA roles, so both are rebuilt here.
 */
const HOURS = Array.from({ length: 24 }, (_, h) => h);

/** 7 -> "07:00". Padded so the two columns line up as columns. */
function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

/** "07:00–08:00" -> [7, 8]; anything else -> [null, null]. */
function parseRange(value: string): [number | null, number | null] {
  const match = /^(\d{1,2}):00\s*[–-]\s*(\d{1,2}):00$/.exec(value ?? "");
  if (!match) return [null, null];
  return [Number(match[1]), Number(match[2])];
}

function validTimeRange(value: string): boolean {
  const [start, end] = parseRange(value);
  return start !== null && end !== null && start >= 0 && end <= 23 && end > start;
}

function TimeSlotSelect({
  value,
  onChange,
  invalid,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  invalid: boolean;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState({ above: false, listHeight: 208 });
  const wrapperRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const [start, end] = parseRange(value);

  useEffect(() => {
    if (!open) return;
    // `mousedown`, not `click`: fires before the trigger's own click handler
    // would otherwise re-open what this just closed on the same press.
    function handlePointerDown(e: MouseEvent) {
      if (!wrapperRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
    };
  }, [open]);

  // A partially-picked value ("07:00–") still needs to light up its column.
  const pendingStart = /^(\d{1,2}):00–$/.exec(value ?? "");
  const pendingEnd = /^–(\d{1,2}):00$/.exec(value ?? "");
  const shownStart = start ?? (pendingStart ? Number(pendingStart[1]) : null);
  const shownEnd = end ?? (pendingEnd ? Number(pendingEnd[1]) : null);
  const complete = validTimeRange(value);

  function togglePicker() {
    if (!open && wrapperRef.current) {
      const trigger = wrapperRef.current.getBoundingClientRect();
      const scroller = wrapperRef.current.closest('[data-daily-scroll]')?.getBoundingClientRect();
      const below = Math.min(scroller?.bottom ?? window.innerHeight, window.innerHeight) - trigger.bottom;
      const above = trigger.top - Math.max(scroller?.top ?? 0, 0);
      const placeAbove = below < 250 && above > below;
      setPlacement({ above: placeAbove, listHeight: Math.min(208, Math.max(48, (placeAbove ? above : below) - 42)) });
    }
    setOpen((previous) => !previous);
  }

  function pick(which: "start" | "end", hour: number) {
    // Read from the *shown* values, not from `parseRange`. A half-made
    // choice is stored as "19:00–", which `parseRange` deliberately rejects
    // as an incomplete range — so reading `start` here would see null and the
    // second click would throw the first one away.
    const nextStart = which === "start" ? hour : shownStart;
    const nextEnd = which === "end" ? hour : shownEnd;

    if (nextStart === null || nextEnd === null || nextEnd <= nextStart) {
      // Keep the partial selection visible; validTimeRange prevents saving
      // until both ends form a forward range.
      onChange(
        which === "start" ? `${hourLabel(hour)}–` : `–${hourLabel(hour)}`,
      );
      return;
    }
    onChange(`${hourLabel(nextStart)}–${hourLabel(nextEnd)}`);
    setOpen(false);
  }

  return (
    <div ref={wrapperRef} className="relative min-w-0" onKeyDown={(event) => {
      if (open && event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); setOpen(false);
        wrapperRef.current?.querySelector<HTMLButtonElement>('[role="combobox"]')?.focus();
      }
    }}>
      <button
        type="button"
        role="combobox"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={label}
        aria-required="true"
        aria-invalid={invalid}
        onClick={togglePicker}
        className={`flex min-h-11 w-full items-center justify-between gap-2 px-3 py-2 tabular-nums ${FIELD_CONTROL} ${
          complete ? "text-ink" : "text-ink-muted"
        } ${invalid ? "border-alert-edge text-alert-ink" : "border-line"}`}
      >
        <span className="truncate">
          {complete ? `${hourLabel(start!)}–${hourLabel(end!)}` : shownStart !== null ? `${hourLabel(shownStart)}–待选结束` : shownEnd !== null ? `待选开始–${hourLabel(shownEnd)}` : "选择活动时间"}
        </span>
        <ChevronDownMark
          className={`h-3 w-3 shrink-0 text-ink-faint transition-transform duration-300 ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-label={label}
          className={`absolute left-0 z-20 w-[13rem] overflow-clip rounded-2xl border border-line bg-sheet depth-float ${placement.above ? "bottom-full mb-1.5" : "top-full mt-1.5"}`}
        >
          <div className="grid grid-cols-2">
            <HourColumn
              heading="开始"
              hours={HOURS}
              selected={shownStart}
              maxHeight={placement.listHeight}
              onPick={(h) => pick("start", h)}
            />
            <div className="border-l border-line">
              <HourColumn
                heading="结束"
                hours={HOURS}
                selected={shownEnd}
                maxHeight={placement.listHeight}
                // An end before the start is a typo, not a night shift — the
                // column greys those out rather than accepting a backwards
                // range and complaining about it afterwards.
                disabledBefore={shownStart}
                onPick={(h) => pick("end", h)}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function HourColumn({
  heading,
  hours,
  selected,
  disabledBefore,
  maxHeight,
  onPick,
}: {
  heading: string;
  hours: number[];
  selected: number | null;
  disabledBefore?: number | null;
  maxHeight: number;
  onPick: (hour: number) => void;
}) {
  const listRef = useRef<HTMLUListElement>(null);

  // Open scrolled to the current choice rather than at midnight — otherwise
  // an evening activity means scrolling past twenty rows every time.
  useEffect(() => {
    if (selected === null) return;
    const list = listRef.current;
    const option = list?.querySelector<HTMLButtonElement>(`[data-hour="${selected}"]`);
    // Scroll only the option list, never the entire daily-record form.
    if (list && option) list.scrollTop = option.offsetTop - (list.clientHeight - option.clientHeight) / 2;
  }, [selected]);

  return (
    <div>
      <p className="sticky top-0 z-10 bg-panel px-3 pb-1.5 pt-2 text-[0.68rem] tracking-wide text-ink-faint">
        {heading}
      </p>
      <ul
        ref={listRef}
        role="listbox"
        aria-label={heading}
        className="zen-scroll relative overflow-y-auto overscroll-contain px-1 pb-1"
        style={{ maxHeight }}
        onKeyDown={(event) => {
          if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const options = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
          const index = options.indexOf(document.activeElement as HTMLButtonElement);
          const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 :
            (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
          options[next]?.focus();
        }}
      >
        {hours.map((hour) => {
          const active = hour === selected;
          const blocked = disabledBefore !== null && disabledBefore !== undefined && hour <= disabledBefore;
          return (
            <li key={hour}>
              <button
                type="button"
                role="option"
                aria-selected={active}
                disabled={blocked}
                data-hour={hour}
                onClick={() => onPick(hour)}
                className={`w-full rounded-lg px-2.5 py-1.5 text-center text-[0.8rem] tabular-nums transition-colors duration-150 ${
                  active
                    ? "bg-accent-wash text-accent-ink"
                    : blocked
                      ? "cursor-not-allowed text-ink-faint/40"
                      : "text-ink-muted hover:bg-raised hover:text-ink"
                }`}
              >
                {hourLabel(hour)}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** 0–5 as six pills. A dropdown hides the range; the whole scale visible at
 *  once is what makes the answer a comparison rather than a recall task. */
function SegmentedScore({
  label,
  hint,
  value,
  onChange,
  required = false,
  compact = false,
}: {
  label: string;
  hint: string;
  value: number | null;
  onChange: (v: number) => void;
  required?: boolean;
  compact?: boolean;
}) {
  const hintId = useId();
  // Secondary scores use short inline labels, not a second stack of large
  // question cards. Their explanations remain available to assistive tech.
  return (
    <div className={compact ? "flex items-center gap-2" : "flex flex-col gap-2"}>
      <div className={compact ? "shrink-0" : "space-y-1"}>
        <span title={compact ? hint : undefined} className={compact ? "text-xs font-medium leading-5 text-ink-muted" : FIELD_LABEL}>
          <span>{label}</span> {required && <RequiredMark />}
        </span>
        {compact && <span id={hintId} className="sr-only">
          {hint}
        </span>}
      </div>

      <div
        role="radiogroup"
        aria-label={`${label}（0 到 5）`}
        aria-describedby={hintId}
        aria-required={required}
        className={`flex flex-1 ${compact ? "gap-0.5" : "gap-1"}`}
        onKeyDown={(event) => {
          const delta = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
          if (!delta && !["Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? 0 : event.key === "End" ? 5 : value === null ? 0 : (value + delta + 6) % 6;
          onChange(next); event.currentTarget.querySelectorAll<HTMLButtonElement>('button')[next]?.focus();
        }}
      >
        {[0, 1, 2, 3, 4, 5].map((n) => {
          const active = n === value;
          return (
            <button
              key={n}
              type="button"
              role="radio"
              aria-checked={active}
              tabIndex={active || (value === null && n === 0) ? 0 : -1}
              aria-label={`${label} ${n}`}
              onClick={() => onChange(n)}
              className={`min-w-0 flex-1 border transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${compact ? "h-9 rounded-lg text-xs" : "h-11 rounded-xl text-sm"} ${
                active
                  ? compact ? "border-transparent bg-accent-wash font-semibold text-accent-ink" : "border-accent bg-accent font-semibold text-on-accent"
                  : compact ? "border-transparent bg-raised/60 text-ink-muted hover:bg-sheet hover:text-ink" : "border-line bg-sheet text-ink hover:border-accent-edge hover:bg-accent-wash"
              }`}
            >
              {n}
            </button>
          );
        })}
      </div>
      {!compact && <span id={hintId} className="block text-xs leading-5 text-ink-muted">{hint}</span>}
    </div>
  );
}

function RequiredMark() {
  return <span className="inline-block shrink-0 align-middle text-xs font-medium text-accent-ink">必填</span>;
}
