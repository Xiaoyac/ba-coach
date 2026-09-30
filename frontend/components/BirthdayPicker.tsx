"use client";

import { useEffect, useId, useRef, useState } from "react";

type Part = "year" | "month" | "day";
const names = { year: "年", month: "月", day: "日" };
const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-edge focus-visible:ring-offset-2 focus-visible:ring-offset-panel";

function todayInShanghai() {
  const parts = new Intl.DateTimeFormat("en", { timeZone: "Asia/Shanghai", year: "numeric", month: "numeric", day: "numeric" }).formatToParts(new Date());
  return ["year", "month", "day"].map((part) => Number(parts.find((p) => p.type === part)?.value));
}
function daysInMonth(year: number, month: number) { return new Date(year, month, 0).getDate(); }
function parse(value: string) { return value ? value.split("-").map(Number) : [0, 0, 0]; }

/** A birthday is chosen explicitly; opening the picker never supplies a date. */
export default function BirthdayPicker({ value, onChange, disabled = false }: {
  value: string; onChange: (value: string) => void; disabled?: boolean;
}) {
  const id = useId();
  const [parts, setParts] = useState(() => parse(value));
  const [open, setOpen] = useState<Part | null>(null);
  const [yearPage, setYearPage] = useState(() => Math.floor((parse(value)[0] || 2000) / 20) * 20);
  const lastEmitted = useRef(value);
  const panel = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const triggers = useRef<Partial<Record<Part, HTMLButtonElement | null>>>({});
  const [year, month, day] = parts;
  const [nowYear, nowMonth, nowDay] = todayInShanghai();
  const age = (y: number, m: number, d: number) => nowYear - y - Number(m > nowMonth || (m === nowMonth && d > nowDay));
  const valid = (y: number, m: number, d: number) => !!(y && m && d && d <= daysInMonth(y, m) && age(y, m, d) >= 10 && age(y, m, d) <= 120);
  const monthAllowed = (y: number, m: number) => valid(y, m, 1) || valid(y, m, daysInMonth(y, m));
  const yearAllowed = (y: number) => Array.from({ length: 12 }, (_, i) => i + 1).some((m) => monthAllowed(y, m));

  useEffect(() => {
    if (value !== lastEmitted.current) {
      setParts(parse(value));
      if (value) setYearPage(Math.floor(parse(value)[0] / 20) * 20);
      lastEmitted.current = value;
    }
  }, [value]);
  useEffect(() => {
    if (open) panel.current?.focus({ preventScroll: true });
  }, [open]);
  useEffect(() => { if (disabled) setOpen(null); }, [disabled]);
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(null);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);

  function choose(part: Part, number: number) {
    const next = [...parts];
    next[part === "year" ? 0 : part === "month" ? 1 : 2] = number;
    if (next[1] && !monthAllowed(next[0], next[1])) { next[1] = 0; next[2] = 0; }
    if (next[2] && !valid(next[0], next[1], next[2])) next[2] = 0;
    setParts(next);
    const result = valid(next[0], next[1], next[2]) ? next.map((n) => String(n).padStart(2, "0")).join("-") : "";
    lastEmitted.current = result;
    onChange(result);
    setOpen(part === "year" ? "month" : part === "month" ? "day" : null);
    if (part === "day") triggers.current.day?.focus();
  }

  const options = open === "year" ? Array.from({ length: 20 }, (_, i) => yearPage + i)
    : open === "month" ? Array.from({ length: 12 }, (_, i) => i + 1)
    : Array.from({ length: year && month ? daysInMonth(year, month) : 31 }, (_, i) => i + 1);
  return <div ref={root} onBlur={(event) => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node)) setOpen(null); }}
    onKeyDownCapture={(event) => {
      if (event.key === "Escape" && open) {
        event.preventDefault(); event.stopPropagation();
        triggers.current[open]?.focus(); setOpen(null);
      }
    }}>
    <div role="group" aria-label="出生日期" className="grid grid-cols-[1.3fr_1fr_1fr] gap-2">
      {(["year", "month", "day"] as const).map((part, index) => <button key={part} ref={(node) => { triggers.current[part] = node; }}
        type="button" disabled={disabled || (part === "month" && !year) || (part === "day" && (!year || !month))}
        aria-label={`出生${names[part]}${parts[index] ? `：${parts[index]}` : "：未选择"}`} aria-expanded={open === part} aria-controls={`${id}-panel`}
        onClick={() => setOpen(open === part ? null : part)}
        className={`${focus} flex min-h-14 min-w-0 items-center justify-between gap-1 rounded-2xl border px-3 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${open === part ? "border-accent-edge bg-accent-wash text-accent-ink" : "border-line bg-raised text-ink hover:border-accent-edge"}`}>
        <span className="tabular-nums">{parts[index] || (part === "year" ? "年份" : part === "month" ? "月份" : "日期")}{!!parts[index] && <span className="ml-1 text-xs text-ink-faint">{names[part]}</span>}</span>
        <svg aria-hidden="true" width="12" height="12" viewBox="0 0 16 16" fill="none" className={`shrink-0 transition-transform ${open === part ? "rotate-180" : ""}`}><path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>)}
    </div>
    {open && <div id={`${id}-panel`} ref={panel} tabIndex={-1} role="group" aria-label={`选择出生${names[open]}`}
      className="mt-2 rounded-2xl border border-accent-edge bg-panel p-3 shadow-sm outline-none">
      <div className="mb-3 flex min-h-9 items-center justify-between gap-2">
        <span className="pl-1 text-xs font-medium text-ink-muted">{open === "year" ? `${yearPage} — ${yearPage + 19}` : open === "month" ? `${year} 年 · 选择月份` : `${year} 年 ${month} 月 · 选择日期`}</span>
        <div className="flex gap-1">
          {open === "year" && <>
            <button type="button" aria-label="往前 20 年" disabled={yearPage <= nowYear - 121} onClick={() => setYearPage((n) => n - 20)} className={`${focus} min-h-11 min-w-11 rounded-xl text-ink-muted hover:bg-raised disabled:opacity-25`}>←</button>
            <button type="button" aria-label="往后 20 年" disabled={yearPage + 20 > nowYear - 10} onClick={() => setYearPage((n) => n + 20)} className={`${focus} min-h-11 min-w-11 rounded-xl text-ink-muted hover:bg-raised disabled:opacity-25`}>→</button>
          </>}
          <button type="button" aria-label="收起生日选择" onClick={() => { triggers.current[open]?.focus(); setOpen(null); }} className={`${focus} min-h-11 min-w-11 rounded-xl text-lg text-ink-faint hover:bg-raised`}>×</button>
        </div>
      </div>
      <div className={`grid gap-1 ${open === "day" ? "grid-cols-7" : "grid-cols-4"}`}>
        {options.map((n) => {
          const selected = n === parts[open === "year" ? 0 : open === "month" ? 1 : 2];
          const allowed = open === "year" ? yearAllowed(n) : open === "month" ? monthAllowed(year, n) : valid(year, month, n);
          return <button key={n} type="button" aria-label={`${n}${names[open]}`} aria-pressed={selected} disabled={!allowed || disabled} onClick={() => choose(open, n)}
            className={`${focus} min-h-11 rounded-xl text-sm tabular-nums transition-colors disabled:cursor-not-allowed disabled:opacity-20 ${selected ? "bg-accent text-on-accent" : "text-ink hover:bg-accent-wash hover:text-accent-ink"}`}>{n}{open === "month" ? "月" : ""}</button>;
        })}
      </div>
    </div>}
    <p aria-live="polite" className="mt-2 pl-1 text-[0.7rem] leading-5 text-ink-faint">{valid(year, month, day) ? `${year} 年 ${month} 月 ${day} 日 · ${age(year, month, day)} 岁` : "依次选择年、月、日，不会自动填写"}</p>
  </div>;
}
