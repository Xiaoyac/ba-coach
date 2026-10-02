"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { ConversationReplyEffort } from "@/lib/conversations";

export function SettingHelp({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  return <div ref={root} className="sm:relative">
    <button type="button" aria-label={label} aria-expanded={open} aria-controls={id}
      onClick={() => setOpen(value => !value)}
      className="grid h-7 w-6 place-items-center rounded-full text-ink-muted hover:bg-raised focus-visible:outline-2 focus-visible:outline-accent">
      <span aria-hidden="true" className="grid h-3.5 w-3.5 place-items-center rounded-full border border-current text-[10px] leading-none">?</span>
    </button>
    {open && <div id={id} role="note" className="absolute left-2 right-2 top-full z-40 sm:left-auto sm:right-0 sm:w-64 rounded-xl border border-line bg-panel p-3 text-xs leading-6 text-ink shadow-lg">{children}</div>}
  </div>;
}

export function ReplyEffortSlider({ value, options, disabled, onChange }: {
  value: ConversationReplyEffort; options: ConversationReplyEffort[]; disabled: boolean;
  onChange: (value: ConversationReplyEffort) => void;
}) {
  const index = Math.max(0, options.indexOf(value));
  const [draft, setDraft] = useState(index);
  const pending = useRef(index);
  useEffect(() => { setDraft(index); pending.current = index; }, [index, disabled]);
  const commit = () => { if (!disabled && options[pending.current] !== value) onChange(options[pending.current]); };
  const labels = options.map(option => option === "max" ? "xhigh" : option);
  return <div className={`relative isolate h-8 w-36 shrink-0 rounded-full border border-line bg-raised p-1 sm:w-44 ${disabled ? "opacity-50" : ""}`}>
    <span aria-hidden="true" className="absolute bottom-1 top-1 rounded-full bg-panel shadow-sm transition-[left] duration-150 motion-reduce:transition-none"
      style={{ width: `calc((100% - 8px) / ${options.length})`, left: `calc(4px + (100% - 8px) * ${draft} / ${options.length})` }} />
    <div aria-hidden="true" className="pointer-events-none relative grid h-full items-center text-center text-[11px] sm:text-xs" style={{ gridTemplateColumns: `repeat(${options.length}, 1fr)` }}>
      {labels.map((label, i) => <span key={label} className={draft === i ? "font-semibold text-ink" : "text-ink-muted"}>{label}</span>)}
    </div>
    <input id="conversation-reply-effort" type="range" min={0} max={Math.max(0, options.length - 1)} step={1} value={draft}
      aria-label="主回复思考强度" aria-valuetext={labels[draft]}
      disabled={disabled || options.length < 2}
      onChange={event => { const next = Number(event.target.value); pending.current = next; setDraft(next); }}
      onPointerUp={commit} onKeyUp={commit} onBlur={commit}
      onPointerCancel={() => { pending.current = index; setDraft(index); }}
      className="effort-slider absolute inset-0 h-full w-full cursor-pointer appearance-none rounded-full bg-transparent focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-wait"
      style={{ "--effort-thumb-width": `${100 / options.length}%` } as CSSProperties} />
    <style jsx>{`
      .effort-slider::-webkit-slider-thumb { appearance: none; width: var(--effort-thumb-width); height: 32px; background: transparent; }
      .effort-slider::-moz-range-thumb { width: var(--effort-thumb-width); height: 32px; background: transparent; border: 0; }
      .effort-slider::-moz-range-track { background: transparent; }
    `}</style>
  </div>;
}
