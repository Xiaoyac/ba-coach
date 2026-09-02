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
  { key: "emotion", label: "情绪", hint: "当时的心情" },
  { key: "achievement", label: "成就", hint: "做成了什么" },
  { key: "connection", label: "联结", hint: "与人的靠近" },
  { key: "enjoyment", label: "愉悦", hint: "享受的程度" },
  { key: "importance", label: "重要", hint: "对你的意义" },
];

type SummaryField = Exclude<keyof DailySummary, "reflection_note">;

const SUMMARY_SCALES: {
  key: SummaryField;
  label: string;
  low: string;
  high: string;
}[] = [
  { key: "completion_rate", label: "完成度", low: "几乎没做", high: "都完成了" },
  { key: "activity_level", label: "活动量", low: "整天静止", high: "非常活跃" },
  { key: "social_connection", label: "social 联结", low: "完全独处", high: "紧密相连" },
  {
    key: "approach_vs_avoidance",
    label: "面对 / 回避",
    low: "完全回避",
    high: "主动面对",
  },
  { key: "overall_mood", label: "整体心情", low: "很低落", high: "很平静愉快" },
];

const emptyActivity = (): ActivityLog => ({
  time_slot: "",
  activity: "",
  emotion: 3,
  achievement: 3,
  connection: 3,
  enjoyment: 3,
  importance: 3,
  note: "",
});

const defaultSummary = (): DailySummary => ({
  completion_rate: 5,
  activity_level: 5,
  social_connection: 5,
  approach_vs_avoidance: 5,
  overall_mood: 5,
  reflection_note: "",
});

export default function DailyAssessmentModal({
  onClose,
}: {
  /** Fired whenever the window goes away — submitted, skipped, the X, or
   *  clicking outside it. This is a manually opened tool now, not a gate
   *  the rest of the app waits on, so the caller only ever needs "it's
   *  closed" and doesn't have to tell those cases apart. */
  onClose: () => void;
}) {
  const [view, setView] = useState<"record" | "history">("record");
  const [step, setStep] = useState<1 | 2>(1);
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
  const titleId = useId();

  // A new step is a new page of content; leaving it scrolled halfway down
  // hides the heading that explains what changed.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  }, [step, view]);

  const filled = activities.filter((a) => a.activity.trim().length > 0);
  const missingTimeSlot = filled.some((a) => a.time_slot.trim().length === 0);
  const canAdvance = filled.length > 0 && !missingTimeSlot;

  function patchActivity(index: number, patch: Partial<ActivityLog>) {
    setActivities((prev) =>
      prev.map((a, i) => (i === index ? { ...a, ...patch } : a)),
    );
  }

  function addActivity() {
    setActivities((prev) => [...prev, emptyActivity()]);
  }

  function removeActivity(index: number) {
    // Never leave the step with zero cards — an empty step 1 gives the user
    // nothing to act on and no obvious way back.
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
    if (busy) return;
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
          reflection_note: summary.reflection_note?.trim()
            ? summary.reflection_note.trim()
            : null,
        },
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
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
    <div
      className="zen-overlay-enter fixed inset-0 z-40 flex items-center justify-center overflow-clip bg-canvas/70 px-4 py-5 backdrop-blur-md sm:py-8"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onClick={handleDismiss}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="flex h-full min-h-0 w-full max-w-2xl flex-col overflow-clip rounded-[28px] border border-line bg-panel depth-panel backdrop-blur-2xl"
      >
        {/* ---- header ---- */}
        <header className="shrink-0 border-b border-line px-5 py-4 sm:px-7">
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
                  : step === 1
                    ? "记下今天做过的事 · 不需要写得完整"
                    : "回看这一天 · 凭感觉就好"}
              </p>
            </div>
            <span className="ml-auto shrink-0 rounded-full border border-accent-edge bg-accent-wash px-3 py-1 text-[0.7rem] text-accent-ink">
              {view === "history" ? "历史" : `${step} / 2`}
            </span>
            <button
              type="button"
              onClick={handleDismiss}
              aria-label="关闭"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-ink-faint transition-colors duration-300 hover:bg-raised hover:text-ink"
            >
              <CloseMark className="h-3.5 w-3.5" />
            </button>
          </div>

          {view === "record" && (
            <div className="mt-3 flex gap-1.5" aria-hidden>
              {[1, 2].map((s) => (
                <span
                  key={s}
                  className={`h-1 flex-1 rounded-full transition-colors duration-500 ${
                    s <= step ? "bg-accent" : "bg-line"
                  }`}
                />
              ))}
            </div>
          )}
        </header>

        {/* ---- body: the only scrolling region ---- */}
        <div
          ref={scrollRef}
          className="zen-scroll min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-7"
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
          ) : step === 1 ? (
            <div className="space-y-4">
              {activities.map((a, i) => (
                <ActivityCard
                  key={i}
                  index={i}
                  value={a}
                  onChange={(patch) => patchActivity(i, patch)}
                  onRemove={() => removeActivity(i)}
                  removable={activities.length > 1}
                />
              ))}

              {/* Solid `bg-accent` + `text-canvas` rather than the translucent
                  `accent-edge`/`accent-wash` pill used everywhere else — this
                  is the one control in the step that should out-rank the
                  cards above it, not blend into their register. */}
              <button
                type="button"
                onClick={addActivity}
                className="flex w-full items-center justify-center gap-2 rounded-2xl bg-accent px-6 py-4 text-[0.9rem] font-medium text-canvas depth-float transition-all duration-300 hover:-translate-y-0.5 hover:brightness-110 active:translate-y-0"
              >
                <PlusMark className="h-4 w-4" />
                添加一项活动
              </button>
            </div>
          ) : (
            <div className="space-y-7">
              {SUMMARY_SCALES.map((s) => (
                <SliderRow
                  key={s.key}
                  label={s.label}
                  low={s.low}
                  high={s.high}
                  value={summary[s.key]}
                  onChange={(v) => setSummary((prev) => ({ ...prev, [s.key]: v }))}
                />
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
                  rows={3}
                  placeholder="今天有什么想记下来的…"
                  className="zen-scroll w-full resize-none rounded-2xl border border-line bg-raised px-4 py-3 text-[0.9rem] leading-[1.8] text-ink placeholder:text-ink-faint transition-colors duration-300 focus:border-accent-edge focus:outline-none"
                />
              </label>
            </div>
          )}
        </div>

        {/* ---- footer ---- */}
        <div className="shrink-0 border-t border-line px-5 py-4 sm:px-7">
          {view === "record" && error && (
            <p className="mb-3 rounded-2xl border border-alert-edge bg-alert-wash px-4 py-2.5 text-[0.82rem] leading-relaxed text-alert-ink">
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
          <div className="flex items-center justify-between gap-3">
            <button
              type="button"
              onClick={showHistory}
              disabled={busy}
              className="flex items-center gap-2 rounded-full px-2 py-2 text-[0.8rem] text-ink-faint transition-colors duration-300 hover:bg-accent-wash hover:text-accent-ink disabled:opacity-40 sm:px-3"
            >
              <HistoryMark className="h-4 w-4" />
              查看历史
            </button>

            <div className="flex items-center justify-end gap-2">
              {step === 2 && (
              <button
                type="button"
                onClick={() => setStep(1)}
                disabled={busy}
                className="rounded-full border border-line px-4 py-2 text-[0.82rem] text-ink-muted transition-colors duration-300 hover:border-accent-edge hover:text-accent-ink disabled:opacity-40"
              >
                上一步
              </button>
              )}

              {step === 1 ? (
              <button
                type="button"
                onClick={() => setStep(2)}
                disabled={!canAdvance}
                title={
                  canAdvance
                    ? undefined
                    : filled.length === 0
                      ? "先写下至少一项活动"
                      : "请为每一项活动选择时间段"
                }
                className="rounded-full bg-accent-edge px-5 py-2 text-[0.82rem] text-accent-ink transition-all duration-300 hover:bg-accent-wash disabled:cursor-not-allowed disabled:opacity-40"
              >
                下一步
              </button>
              ) : (
              <button
                type="button"
                onClick={handleSubmit}
                disabled={busy}
                className="flex items-center gap-2 rounded-full bg-accent-edge px-5 py-2 text-[0.82rem] text-accent-ink transition-all duration-300 hover:bg-accent-wash disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy && (
                  <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line-strong border-t-accent motion-reduce:animate-none" />
                )}
                完成记录
              </button>
              )}
            </div>
          </div>
          )}
        </div>
      </div>
    </div>
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
    value.activity.trim().length > 0 && value.time_slot.trim().length === 0;

  return (
    <section className="rounded-2xl border border-line bg-raised p-4 depth-bubble sm:p-5">
      <div className="flex items-center gap-3">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-accent-edge bg-accent-wash text-[0.7rem] text-accent-ink">
          {index + 1}
        </span>
        <TimeSlotSelect
          value={value.time_slot}
          onChange={(time_slot) => onChange({ time_slot })}
          invalid={timeSlotMissing}
          label={`活动 ${index + 1} 的时间段`}
        />
        {removable && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={`删除活动 ${index + 1}`}
            className="ml-auto flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-ink-faint transition-colors duration-300 hover:bg-alert-wash hover:text-alert-ink"
          >
            <CloseMark className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      <input
        value={value.activity}
        onChange={(e) => onChange({ activity: e.target.value })}
        // Concrete examples, not just "做了什么…": a blank prompt invites
        // abstractions ("休息了一下"), and the whole point of the record is
        // one nameable activity per row.
        placeholder="做了什么…例如：散步、打羽毛球、做饭"
        aria-label={`活动 ${index + 1} 的内容`}
        className="mt-3 w-full border-b border-line bg-transparent pb-2 text-[0.95rem] leading-relaxed text-ink placeholder:text-ink-faint transition-colors duration-300 focus:border-accent-edge focus:outline-none"
      />

      <div className="mt-4 space-y-3">
        {ACTIVITY_SCALES.map((s) => (
          <SegmentedScore
            key={s.key}
            label={s.label}
            hint={s.hint}
            value={value[s.key]}
            onChange={(v) => onChange({ [s.key]: v } as Partial<ActivityLog>)}
          />
        ))}
      </div>

      <input
        value={value.note ?? ""}
        onChange={(e) => onChange({ note: e.target.value })}
        placeholder="一句备注（可留空）"
        aria-label={`活动 ${index + 1} 的备注`}
        className="mt-4 w-full rounded-xl border border-line bg-transparent px-3 py-2 text-[0.82rem] text-ink placeholder:text-ink-faint transition-colors duration-300 focus:border-accent-edge focus:outline-none"
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
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  // A partially-picked value ("07:00–") still needs to light up its column.
  const pendingStart = /^(\d{1,2}):00–$/.exec(value ?? "");
  const pendingEnd = /^–(\d{1,2}):00$/.exec(value ?? "");
  const shownStart = start ?? (pendingStart ? Number(pendingStart[1]) : null);
  const shownEnd = end ?? (pendingEnd ? Number(pendingEnd[1]) : null);
  const complete = start !== null && end !== null;

  function pick(which: "start" | "end", hour: number) {
    // Read from the *shown* values, not from `parseRange`. A half-made
    // choice is stored as "19:00–", which `parseRange` deliberately rejects
    // as an incomplete range — so reading `start` here would see null and the
    // second click would throw the first one away.
    const nextStart = which === "start" ? hour : shownStart;
    const nextEnd = which === "end" ? hour : shownEnd;

    if (nextStart === null || nextEnd === null) {
      // Only half chosen so far. Hold it in the field so the column shows the
      // selection, but don't write a range that has no end — a half range
      // would still satisfy the "did you pick a time" check downstream.
      onChange(
        which === "start" ? `${hourLabel(hour)}–` : `–${hourLabel(hour)}`,
      );
      return;
    }
    onChange(`${hourLabel(nextStart)}–${hourLabel(nextEnd)}`);
    setOpen(false);
  }

  return (
    <div ref={wrapperRef} className="relative shrink-0">
      <button
        type="button"
        role="combobox"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={label}
        aria-invalid={invalid}
        onClick={() => setOpen((o) => !o)}
        className={`flex w-[9.5rem] items-center justify-between gap-1.5 rounded-full border bg-raised px-3 py-1.5 text-[0.8rem] tabular-nums transition-colors duration-300 focus:outline-none ${
          complete ? "text-ink" : "text-ink-faint"
        } ${invalid ? "border-alert-edge text-alert-ink" : "border-accent-edge"}`}
      >
        <span className="truncate">
          {complete ? `${hourLabel(start)}–${hourLabel(end)}` : "几点到几点"}
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
          className="absolute left-0 top-full z-20 mt-1.5 w-[13rem] overflow-clip rounded-2xl border border-line bg-panel depth-float"
        >
          <div className="grid grid-cols-2">
            <HourColumn
              heading="开始"
              hours={HOURS}
              selected={shownStart}
              onPick={(h) => pick("start", h)}
            />
            <div className="border-l border-line">
              <HourColumn
                heading="结束"
                hours={HOURS}
                selected={shownEnd}
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
  onPick,
}: {
  heading: string;
  hours: number[];
  selected: number | null;
  disabledBefore?: number | null;
  onPick: (hour: number) => void;
}) {
  const listRef = useRef<HTMLUListElement>(null);

  // Open scrolled to the current choice rather than at midnight — otherwise
  // an evening activity means scrolling past twenty rows every time.
  useEffect(() => {
    if (selected === null) return;
    listRef.current
      ?.querySelector(`[data-hour="${selected}"]`)
      ?.scrollIntoView({ block: "center" });
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
        className="zen-scroll max-h-52 overflow-y-auto px-1 pb-1"
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
}: {
  label: string;
  hint: string;
  value: number;
  onChange: (v: number) => void;
}) {
  // Stacked on phones, inline from `sm` up. Side-by-side costs the control
  // ~76px of the row, which at 375px squeezes each pill to under 30px — too
  // small to hit reliably, and there are thirty of them per card.
  return (
    <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
      <div className="flex shrink-0 items-baseline gap-2 sm:w-16 sm:block">
        <span className="text-[0.8rem] leading-tight text-ink-muted sm:block">
          {label}
        </span>
        <span className="text-[0.65rem] leading-tight text-ink-faint sm:block">
          {hint}
        </span>
      </div>

      <div
        role="radiogroup"
        aria-label={`${label}（0 到 5）`}
        className="flex flex-1 gap-0.5 sm:gap-1"
      >
        {[0, 1, 2, 3, 4, 5].map((n) => {
          const active = n === value;
          return (
            <button
              key={n}
              type="button"
              role="radio"
              aria-checked={active}
              aria-label={`${label} ${n}`}
              onClick={() => onChange(n)}
              className={`h-11 flex-1 rounded-lg border text-[0.75rem] transition-all duration-300 sm:h-8 ${
                active
                  ? "border-accent-edge bg-accent-wash text-accent-ink"
                  : "border-line text-ink-faint hover:border-accent-edge hover:text-ink-muted"
              }`}
            >
              {n}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function SliderRow({
  label,
  low,
  high,
  value,
  onChange,
}: {
  label: string;
  low: string;
  high: string;
  value: number;
  onChange: (v: number) => void;
}) {
  const id = useId();
  return (
    <div>
      <div className="mb-2 flex items-baseline gap-3">
        <label htmlFor={id} className="text-[0.9rem] text-ink">
          {label}
        </label>
        <span className="ml-auto text-[0.95rem] tabular-nums text-accent-ink">
          {value}
        </span>
      </div>

      <input
        id={id}
        type="range"
        min={0}
        max={10}
        step={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="zen-range"
      />

      {/* The anchors are what make the number mean anything — a bare 0–10 asks
          the user to invent their own scale. */}
      <div className="mt-1 flex justify-between text-[0.7rem] text-ink-faint">
        <span>{low}</span>
        <span>{high}</span>
      </div>
    </div>
  );
}
