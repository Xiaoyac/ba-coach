"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { MeasureActivity, MeasureEmotions, MeasureOverall, MeasureSleep, MeasureSummary } from "@/components/DailyMeasureFields";
import {
  fetchMeasurementByDate,
  fetchMeasurementHistory,
  submitMeasurement,
  updateMeasurement,
  type MeasurementAnswers,
  type MeasurementRecord,
} from "@/lib/dailyMeasurement";
import {
  MEASURE_DATE_GUIDE,
  MEASURE_DAY_INSTRUCTIONS,
  MEASURE_INTRO,
  MEASURE_KEYS,
  emptyMeasure,
  measureProblem,
  type MeasureDraft,
} from "@/lib/dailyQuestionnaire";

type View = "form" | "saved" | "history";

function localToday() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

const toDraft = (record: MeasurementRecord): MeasureDraft =>
  Object.fromEntries(MEASURE_KEYS.map(k => [k, record.answers[k]])) as MeasureDraft;

const message = (error: unknown) => (error instanceof Error ? error.message : "每日测量暂时无法保存，请稍后重试。");

/** 每日测量 tab: the study's daily questionnaire, separate from 每日记录. */
export default function DailyMeasurePanel({ onDraftStateChange }: {
  onDraftStateChange?: (state: { dirty: boolean; busy: boolean }) => void;
}) {
  const titleId = useId();
  const scrollRef = useRef<HTMLDivElement>(null);
  const touched = useRef(false);
  const autoOpened = useRef(false);
  const [view, setView] = useState<View>("form");
  const [recordDate, setRecordDate] = useState(localToday);
  const [draft, setDraft] = useState<MeasureDraft>(emptyMeasure);
  const [editing, setEditing] = useState<MeasurementRecord | null>(null);
  const [saved, setSaved] = useState<MeasurementRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dateExisting, setDateExisting] = useState<MeasurementRecord | null>(null);
  const [dateLoading, setDateLoading] = useState(false);
  const [dateError, setDateError] = useState<string | null>(null);
  const [history, setHistory] = useState<MeasurementRecord[]>([]);
  const [historyMore, setHistoryMore] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  useEffect(() => { onDraftStateChange?.({ dirty: touched.current, busy }); }, [draft, recordDate, editing, view, busy, onDraftStateChange]);
  useEffect(() => { scrollRef.current?.scrollTo({ top: 0 }); }, [view]);

  const openRecord = useCallback((record: MeasurementRecord) => {
    touched.current = false;
    setEditing(record); setRecordDate(record.local_date); setDraft(toDraft(record));
    setDateExisting(null); setError(null); setView("form");
  }, []);

  // A day can hold one 每日测量. Look the chosen date up before a new one is filled in.
  useEffect(() => {
    setDateExisting(null); setDateError(null);
    if (editing || !recordDate || recordDate > localToday()) { setDateLoading(false); return; }
    const control = new AbortController();
    setDateLoading(true);
    fetchMeasurementByDate(recordDate, control.signal)
      .then(record => {
        if (control.signal.aborted) return;
        // Opening the tab on a day already measured shows that day's answers, once.
        if (!autoOpened.current) {
          autoOpened.current = true;
          if (record && !touched.current) { setSaved(record); setView(current => current === "form" ? "saved" : current); }
        }
        setDateExisting(record);
      })
      .catch(err => { if (!control.signal.aborted) setDateError(message(err)); })
      .finally(() => { if (!control.signal.aborted) setDateLoading(false); });
    return () => control.abort();
  }, [recordDate, editing]);

  async function loadHistory(offset: number) {
    setHistoryLoading(true); setHistoryError(null);
    try {
      const page = await fetchMeasurementHistory(offset);
      setHistory(previous => offset === 0 ? page.items : [...previous, ...page.items]);
      setHistoryMore(page.has_more);
    } catch (err) {
      setHistoryError(message(err));
    } finally {
      setHistoryLoading(false);
    }
  }

  function showHistory() { setView("history"); void loadHistory(0); }

  function newMeasurement() {
    touched.current = false;
    setEditing(null); setSaved(null); setRecordDate(localToday()); setDraft(emptyMeasure()); setError(null); setView("form");
  }

  function patch(change: Partial<MeasureDraft>) {
    touched.current = true;
    setDraft(previous => ({ ...previous, ...change }));
  }

  const problem = measureProblem(draft);
  const dateConflict = !editing && dateExisting !== null;
  const futureDate = !recordDate || recordDate > localToday();
  const canSubmit = !problem && !futureDate && !dateLoading && !dateError && !dateConflict && !busy;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true); setError(null);
    try {
      const answers = draft as MeasurementAnswers;
      const record = editing
        ? await updateMeasurement(editing.id, { local_date: recordDate, expected_revision: editing.revision_no, answers })
        : await submitMeasurement(recordDate, answers);
      touched.current = false;
      setSaved(record); setEditing(null); setDateExisting(record); setView("saved");
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  const title = view === "history" ? "每日测量历史" : view === "saved" ? "每日测量已提交" : editing ? "修改每日测量" : "每日测量";
  return (
    <form onSubmit={submit} aria-labelledby={titleId} aria-busy={busy} className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-3 px-5 pb-4 pt-5 sm:px-7">
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="truncate text-[1.05rem] font-medium tracking-wide text-ink">{title}</h2>
          <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">每天一份的研究问卷，与“每日记录”分开保存。</p>
        </div>
        {view === "history"
          ? <button type="button" onClick={() => (editing ? setView("form") : newMeasurement())} className="min-h-11 rounded-full border border-line px-4 text-sm text-ink-muted hover:border-accent-edge hover:text-accent-ink">返回填写</button>
          : <button type="button" disabled={busy} onClick={showHistory} className="min-h-11 rounded-full border border-line px-4 text-sm text-ink-muted hover:border-accent-edge hover:text-accent-ink disabled:opacity-40">查看历史</button>}
      </header>

      <div ref={scrollRef} className="zen-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-5 sm:px-7">
        {view === "history" ? (
          <div className="space-y-3" aria-live="polite">
            {historyError && <p role="alert" className="rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{historyError} <button type="button" className="underline" onClick={() => void loadHistory(0)}>重试</button></p>}
            {!historyLoading && !historyError && history.length === 0 && <p className="rounded-2xl bg-raised p-5 text-sm text-ink-muted">还没有提交过每日测量。</p>}
            {history.map(record => (
              <article key={record.id} className="space-y-3 rounded-2xl border border-line p-4">
                <div className="flex items-center justify-between gap-3">
                  <p className="font-semibold tabular-nums">{record.local_date}{record.revision_no > 1 && <span className="ml-2 text-xs font-normal text-ink-muted">已修改 {record.revision_no - 1} 次</span>}</p>
                  <button type="button" onClick={() => openRecord(record)} className="min-h-11 rounded-full px-3 text-sm text-accent-ink hover:bg-accent-wash">修改</button>
                </div>
                <MeasureSummary answers={toDraft(record)} compact />
              </article>
            ))}
            {historyLoading && <p className="text-sm text-ink-muted">正在读取…</p>}
            {historyMore && !historyLoading && <button type="button" onClick={() => void loadHistory(history.length)} className="min-h-11 w-full rounded-xl border border-line text-sm text-ink-muted hover:bg-raised">加载更多</button>}
          </div>
        ) : view === "saved" && saved ? (
          <section aria-label="本次每日测量" className="space-y-5">
            <div className="rounded-2xl border border-accent-edge bg-accent-wash p-5">
              <p role="status" className="text-base font-semibold">{saved.local_date} 的每日测量已提交</p>
              <p className="mt-2 text-sm leading-6 text-ink-muted">感谢您的认真填写。如果填错了，可以点“修改这份测量”更正，修改历史会保留。</p>
            </div>
            <MeasureSummary answers={toDraft(saved)} />
          </section>
        ) : (
          <fieldset disabled={busy} className="min-w-0 space-y-5">
            {!editing && <div className="space-y-1.5 rounded-2xl border border-accent-edge bg-accent-wash px-4 py-3 text-sm leading-6 text-ink-muted">
              {MEASURE_INTRO.map(line => <p key={line}>{line}</p>)}
            </div>}
            <div className="space-y-2">
              <label className="flex flex-wrap items-center gap-3 text-sm font-medium text-ink">
                记录日期
                <input type="date" required value={recordDate} max={localToday()}
                  onChange={event => { touched.current = true; setRecordDate(event.target.value); }}
                  className="min-h-11 rounded-xl border border-line bg-sheet px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent" />
                {editing && <button type="button" onClick={newMeasurement} className="min-h-11 px-3 text-sm font-normal text-accent-ink">新建测量</button>}
              </label>
              {editing
                ? <p className="text-xs text-ink-muted">正在修改 {editing.local_date} 的每日测量，原答案已带入，保存后会保留修改历史。</p>
                : <div className="text-xs leading-5 text-ink-muted">{MEASURE_DATE_GUIDE.map(line => <p key={line}>{line}</p>)}</div>}
              {futureDate && <p role="alert" className="text-xs text-alert-ink">请选择今天或之前的日期。</p>}
              {dateLoading && <p className="text-xs text-ink-muted">正在读取所选日期…</p>}
              {dateError && <p role="alert" className="text-xs text-alert-ink">{dateError}</p>}
              {dateConflict && dateExisting && <div role="alert" className="rounded-xl bg-accent-wash p-3 text-sm">
                {dateExisting.local_date} 已提交过每日测量，不能重复提交。
                <button type="button" onClick={() => openRecord(dateExisting)} className="mt-2 block min-h-11 rounded-full border border-accent-edge px-4 text-accent-ink">打开这一天的测量</button>
              </div>}
            </div>
            <MeasureSleep value={draft} onChange={patch} />
            <div className="space-y-1 rounded-2xl border border-line px-4 py-3 text-sm leading-6 text-ink-muted">
              {MEASURE_DAY_INSTRUCTIONS.map(line => <p key={line}>{line}</p>)}
            </div>
            <MeasureOverall value={draft} onChange={patch} />
            <MeasureEmotions value={draft} onChange={patch} />
            <MeasureActivity value={draft} onChange={patch} />
          </fieldset>
        )}
      </div>

      {view !== "history" && <footer className="shrink-0 border-t border-line px-5 py-3 sm:px-7">
        {view === "saved" && saved ? (
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button type="button" onClick={() => openRecord(saved)} className="flex min-h-11 items-center rounded-full border border-line px-5 text-sm text-ink-muted hover:border-accent-edge hover:text-accent-ink">修改这份测量</button>
            <button type="button" onClick={showHistory} className="flex min-h-11 items-center rounded-full bg-accent px-5 text-sm font-medium text-on-accent">查看历史</button>
          </div>
        ) : view === "form" ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p role="status" className="min-w-0 flex-1 text-xs leading-5 text-ink-muted">
              {error ? <span className="text-alert-ink">{error}</span> : dateConflict ? "这一天已有每日测量" : problem ?? "全部题目已完成，可以提交"}
            </p>
            <button type="submit" disabled={!canSubmit} className="min-h-11 rounded-full bg-accent px-6 text-sm font-medium text-on-accent disabled:opacity-40">
              {busy ? "正在提交…" : editing ? "保存修改" : "提交"}
            </button>
          </div>
        ) : null}
      </footer>}
    </form>
  );
}
