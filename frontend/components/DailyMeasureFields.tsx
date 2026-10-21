"use client";

import { useEffect, useId, useState, type KeyboardEvent } from "react";
import {
  MEASURE_ACTIVITY_COLUMNS,
  MEASURE_ACTIVITY_EXAMPLE,
  MEASURE_ACTIVITY_INSTRUCTIONS,
  MEASURE_AGREEMENT,
  MEASURE_EMOTION_QUESTION,
  MEASURE_EMOTIONS,
  MEASURE_INTENSITIES,
  MEASURE_DAY_MINUTES,
  MEASURE_MAX_COUNT,
  MEASURE_MAX_MINUTES,
  MEASURE_MAX_SLEEP_HOURS,
  MEASURE_OVERALL,
  MEASURE_SLEEP,
  type MeasureDraft,
  type MeasureKey,
} from "@/lib/dailyQuestionnaire";

type FieldProps = { value: MeasureDraft; onChange: (patch: Partial<MeasureDraft>) => void };

const CONTROL = "rounded-xl border bg-sheet text-sm text-ink transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
const OPTION = "min-h-11 min-w-0 rounded-xl border px-2 text-sm leading-5 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
const OPTION_ON = "border-accent bg-accent font-semibold text-on-accent";
const OPTION_OFF = "border-line bg-sheet text-ink hover:border-accent-edge hover:bg-accent-wash";
const REQUIRED = <span className="text-xs font-medium text-accent-ink">必填</span>;

function Section({ title, meta, children }: { title: string; meta: string; children: React.ReactNode }) {
  const titleId = useId();
  return (
    <section aria-labelledby={titleId} className="min-w-0 space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 id={titleId} className="text-base font-semibold">{title}</h3>
        <span className="text-xs text-ink-muted">{meta}</span>
      </div>
      <div className="space-y-5 rounded-2xl bg-raised p-4 sm:p-5">{children}</div>
    </section>
  );
}

/** A single-choice row stored 1…n; arrow keys move the choice like native radios. */
function Choice({ labelledBy, field, options, value, onChange, numbered = false, className }: {
  labelledBy: string; field: MeasureKey; options: string[]; value: MeasureDraft;
  onChange: FieldProps["onChange"]; numbered?: boolean; className: string;
}) {
  const current = value[field];
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const delta = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
    if (!delta) return;
    event.preventDefault();
    const next = current === null ? 1 : Math.min(options.length, Math.max(1, current + delta));
    onChange({ [field]: next });
    event.currentTarget.querySelectorAll<HTMLButtonElement>("button")[next - 1]?.focus();
  }
  return (
    <div role="radiogroup" aria-labelledby={labelledBy} aria-required="true" className={className} onKeyDown={onKeyDown}>
      {options.map((text, i) => {
        const n = i + 1, active = current === n;
        return <button key={n} type="button" role="radio" aria-checked={active} aria-label={numbered ? `${n} ${text}` : undefined}
          tabIndex={active || (current === null && n === 1) ? 0 : -1}
          onClick={() => onChange({ [field]: n })} className={`${OPTION} ${active ? OPTION_ON : OPTION_OFF}`}>
          {numbered ? n : text}
        </button>;
      })}
    </div>
  );
}

/** Q5–Q6: last night's sleep, hours (one decimal place) and 1 很好 … 4 很差. */
export function MeasureSleep({ value, onChange }: FieldProps) {
  const hoursId = useId(), qualityId = useId();
  const [hoursText, setHoursText] = useState(value.sleep_hours?.toString() ?? "");
  // Follow the draft when another record is loaded, but keep "6." while it is typed.
  useEffect(() => {
    setHoursText(text => parseHours(text) === value.sleep_hours ? text : value.sleep_hours?.toString() ?? "");
  }, [value.sleep_hours]);
  const badFormat = hoursText !== "" && parseHours(hoursText) === null;
  const tooMany = value.sleep_hours !== null && value.sleep_hours > MEASURE_MAX_SLEEP_HOURS;
  const hoursError = badFormat ? "请填写数字，最多保留 1 位小数，如 6.5。" : tooMany ? `最多 ${MEASURE_MAX_SLEEP_HOURS} 小时。` : null;
  return (
    <Section title="昨晚睡眠" meta="2 题">
      <div className="space-y-1.5">
        <label htmlFor={hoursId} className="block text-sm font-medium text-ink">{MEASURE_SLEEP.hours.question} {REQUIRED}</label>
        <div className="flex items-center gap-2">
          <input id={hoursId} type="text" inputMode="decimal" autoComplete="off" required aria-invalid={!!hoursError}
            value={hoursText}
            onChange={event => {
              const text = event.target.value.replace(/[^0-9.]/g, "").slice(0, 5);
              setHoursText(text);
              onChange({ sleep_hours: parseHours(text) });
            }}
            className={`${CONTROL} min-h-11 w-28 min-w-0 px-3 tabular-nums ${hoursError ? "border-alert-edge" : "border-line"}`} />
          <span className="text-xs text-ink-muted">小时</span>
        </div>
        {hoursError ? <p role="alert" className="text-xs leading-5 text-alert-ink">{hoursError}</p>
          : <p className="text-xs leading-5 text-ink-muted">{MEASURE_SLEEP.hours.note}</p>}
      </div>
      <div className="space-y-1.5">
        <p id={qualityId} className="text-sm font-medium text-ink">{MEASURE_SLEEP.quality.question} {REQUIRED}</p>
        <Choice labelledBy={qualityId} field="sleep_quality" options={MEASURE_SLEEP.quality.options} value={value} onChange={onChange}
          className="grid grid-cols-4 gap-1" />
      </div>
    </Section>
  );
}

/** Whole hours or one decimal place ("6", "6.", "6.5"); anything else is not an answer yet. */
function parseHours(text: string): number | null {
  return /^\d{1,2}(\.\d?)?$/.test(text) ? Number(text) : null;
}

/** Q7–Q9: five labelled options each. */
export function MeasureOverall({ value, onChange }: FieldProps) {
  const baseId = useId();
  return (
    <Section title="这一天的整体情况" meta="3 题">
      {MEASURE_OVERALL.map(q => (
        <div key={q.key} className="space-y-2">
          <p id={`${baseId}-${q.key}`} className="text-sm font-medium leading-6 text-ink">{q.question} {REQUIRED}</p>
          <Choice labelledBy={`${baseId}-${q.key}`} field={q.key} options={q.options} value={value} onChange={onChange}
            className="grid grid-cols-1 gap-1 sm:grid-cols-5" />
        </div>
      ))}
    </Section>
  );
}

/** Q10: nine 1–5 agreement items under one shared legend. */
export function MeasureEmotions({ value, onChange }: FieldProps) {
  const baseId = useId();
  return (
    <Section title="这一天的感受" meta="9 项 · 1–5">
      <p className="text-sm leading-6 text-ink">{MEASURE_EMOTION_QUESTION} {REQUIRED}</p>
      <div className="flex gap-3">
        <span aria-hidden="true" className="hidden sm:block sm:w-36 sm:shrink-0" />
        <ol className="grid flex-1 grid-cols-5 gap-1 text-center text-[0.7rem] leading-4 text-ink-muted" aria-label="选项说明">
          {MEASURE_AGREEMENT.map((text, i) => <li key={text}><span className="block font-semibold text-ink tabular-nums">{i + 1}</span>{text}</li>)}
        </ol>
      </div>
      <div className="space-y-2">
        {MEASURE_EMOTIONS.map(item => (
          <div key={item.key} className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
            <span id={`${baseId}-${item.key}`} className="text-sm text-ink sm:w-36 sm:shrink-0">{item.label}</span>
            <Choice labelledBy={`${baseId}-${item.key}`} field={item.key} options={MEASURE_AGREEMENT} numbered value={value} onChange={onChange}
              className="grid flex-1 grid-cols-5 gap-1" />
          </div>
        ))}
      </div>
    </Section>
  );
}

/** Q11: occurrences and total minutes per intensity. Blank is unanswered; 0 is an answer. */
export function MeasureActivity({ value, onChange }: FieldProps) {
  const total = MEASURE_INTENSITIES.reduce((sum, level) => sum + (value[level.minutes] ?? 0), 0);
  return (
    <Section title="这一天的身体活动" meta="次数和累计时长 · 没有就填 0">
      <div className="space-y-1 text-sm leading-6 text-ink-muted">
        {MEASURE_ACTIVITY_INSTRUCTIONS.map(line => <p key={line}>{line}</p>)}
        <p className="rounded-xl bg-sheet px-3 py-2 text-xs leading-5">{MEASURE_ACTIVITY_EXAMPLE}</p>
      </div>
      {MEASURE_INTENSITIES.map((level, i) => (
        <div key={level.name} className="space-y-2 rounded-xl border border-line bg-sheet p-3">
          <div>
            <p className="text-sm font-semibold text-ink">{String.fromCharCode(97 + i)}) {level.name}<span className="font-normal text-ink-muted">（{level.description}）</span></p>
            <p className="text-xs leading-5 text-ink-muted">例如{level.examples}</p>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <NumberField label={MEASURE_ACTIVITY_COLUMNS.count} name={`${level.name}次数`} unit="次" max={MEASURE_MAX_COUNT} value={value[level.count]} onChange={v => onChange({ [level.count]: v })} />
            <NumberField label={MEASURE_ACTIVITY_COLUMNS.minutes} name={`${level.name}累计时长`} unit="分钟" max={MEASURE_MAX_MINUTES} value={value[level.minutes]} onChange={v => onChange({ [level.minutes]: v })} />
          </div>
          {(value[level.minutes] ?? 0) > 0 && value[level.count] === 0 && <p className="text-xs text-alert-ink">填写了累计时长时，次数至少为 1。</p>}
        </div>
      ))}
      {total > MEASURE_DAY_MINUTES && <p role="alert" className="text-xs text-alert-ink">三种强度合计 {total} 分钟，超过了一天的 1440 分钟。</p>}
    </Section>
  );
}

function NumberField({ label, name, unit, max, value, onChange }: {
  label: string; name: string; unit: string; max: number; value: number | null; onChange: (value: number | null) => void;
}) {
  const id = useId();
  const outOfRange = value !== null && value > max;
  return (
    <div className="min-w-0 space-y-1">
      <label htmlFor={id} className="block text-xs leading-5 text-ink-muted">{label} {REQUIRED}</label>
      <div className="flex items-center gap-2">
        <input id={id} type="text" inputMode="numeric" pattern="[0-9]*" autoComplete="off" required aria-invalid={outOfRange}
          aria-label={`${name}（${unit}）`} value={value ?? ""}
          onChange={event => {
            const digits = event.target.value.replace(/[^0-9]/g, "").slice(0, 4);
            onChange(digits === "" ? null : Number(digits));
          }}
          className={`${CONTROL} min-h-11 w-full min-w-0 px-3 tabular-nums ${outOfRange ? "border-alert-edge" : "border-line"}`} />
        <span className="shrink-0 text-xs text-ink-muted">{unit}</span>
      </div>
      {outOfRange && <p className="text-xs text-alert-ink">最多 {max}{unit}</p>}
    </div>
  );
}

/** Read-only answers for the saved view and history. */
export function MeasureSummary({ answers, compact = false }: { answers: MeasureDraft; compact?: boolean }) {
  const option = (options: string[], n: number | null) => (n ? options[n - 1] : "—");
  return (
    <div className={compact ? "space-y-2 text-xs" : "space-y-3 rounded-2xl bg-raised p-4 text-sm"}>
      <dl className="flex flex-wrap gap-x-4 gap-y-1">
        <div className="flex gap-2"><dt className="text-ink-muted">昨晚睡眠</dt><dd className="tabular-nums text-ink">{answers.sleep_hours ?? "—"} 小时</dd></div>
        <div className="flex gap-2"><dt className="text-ink-muted">睡眠质量</dt><dd className="text-ink">{option(MEASURE_SLEEP.quality.options, answers.sleep_quality)}</dd></div>
        {MEASURE_OVERALL.map(q => <div key={q.key} className="flex gap-2"><dt className="text-ink-muted">{q.short}</dt><dd className="text-ink">{option(q.options, answers[q.key])}</dd></div>)}
      </dl>
      <dl className="grid grid-cols-[repeat(auto-fill,minmax(10.5rem,1fr))] gap-x-3 gap-y-1">
        {MEASURE_EMOTIONS.map(item => (
          <div key={item.key} className="flex justify-between gap-2">
            <dt className="truncate text-ink-muted">{item.label}</dt>
            <dd className="tabular-nums text-ink">{answers[item.key] ?? "—"}<span className="text-ink-faint">/5</span></dd>
          </div>
        ))}
      </dl>
      <dl className="grid grid-cols-3 gap-2">
        {MEASURE_INTENSITIES.map(level => (
          <div key={level.name} className={compact ? "" : "rounded-xl bg-sheet p-2 text-center"}>
            <dt className="text-ink-muted">{level.name}</dt>
            <dd className="tabular-nums text-ink">{answers[level.count] ?? "—"} 次 · {answers[level.minutes] ?? "—"} 分钟</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
