"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useParticipantStyle } from "@/lib/useParticipantStyle";
import { SunMark } from "@/components/icons";
import { DEFAULT_THEME, THEME_KEY, THEME_META_COLOR, THEMES, isTheme, type Theme } from "@/lib/theme";

function applyTheme(theme: Theme) {
  for (const option of THEMES) document.documentElement.classList.toggle(`theme-${option.id}`, option.id === theme);
  const simple = document.documentElement.classList.contains("style-simple");
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content",
    theme === "warm" && simple ? "#f8f7f4" : THEME_META_COLOR[theme]);
}

export default function ThemeToggle({ inline = false, embedded = false }: { inline?: boolean; embedded?: boolean }) {
  const participant = useParticipantStyle();
  const names: Record<Theme, string> = { warm: "暖白", porcelain: "青白", github: "深灰", dark: "深蓝" };
  const displayName = (option: typeof THEMES[number]) => participant ? names[option.id] : option.name;
  const [theme, setTheme] = useState<Theme>(DEFAULT_THEME);
  const [position, setPosition] = useState<{ left: number; top?: number; bottom?: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    const sync = () => {
      const active = THEMES.find(option => document.documentElement.classList.contains(`theme-${option.id}`))?.id ?? "dark";
      setTheme(active); applyTheme(active);
    };
    const stored = (event: StorageEvent) => {
      if (event.key === THEME_KEY || event.key === null) {
        applyTheme(isTheme(event.newValue) ? event.newValue : DEFAULT_THEME); sync();
      }
    };
    sync();
    window.addEventListener("themechange", sync);
    window.addEventListener("storage", stored);
    return () => { window.removeEventListener("themechange", sync); window.removeEventListener("storage", stored); };
  }, []);

  useEffect(() => {
    if (!position) return;
    menu.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus();
    const outside = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node) && !button.current?.contains(event.target as Node)) setPosition(null);
    };
    const resize = () => setPosition(null);
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", resize);
    return () => { document.removeEventListener("pointerdown", outside); window.removeEventListener("resize", resize); };
  }, [position]);

  function choose(next: Theme) {
    applyTheme(next); setTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch { /* Selection still works with storage disabled. */ }
    window.dispatchEvent(new Event("themechange"));
    setPosition(null); button.current?.focus();
  }

  function toggle() {
    if (position) { setPosition(null); return; }
    const rect = button.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition({ left: Math.max(12, Math.min(rect.left, window.innerWidth - 272)),
      ...(rect.bottom < window.innerHeight / 2 ? { top: rect.bottom + 8 } : { bottom: window.innerHeight - rect.top + 8 }) });
  }

  const selected = THEMES.find(option => option.id === theme)!;
  if (embedded) return <div role="radiogroup" aria-label="配色方案" className="theme-options"
    onKeyDown={event => {
      if (!["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const current = THEMES.findIndex(option => option.id === theme);
      const next = event.key === "Home" ? 0 : event.key === "End" ? THEMES.length - 1 :
        (current + (["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : -1) + THEMES.length) % THEMES.length;
      choose(THEMES[next].id);
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]')[next]?.focus();
    }}>
    {THEMES.map(option => <button key={option.id} type="button" role="radio" aria-checked={theme === option.id} onClick={() => choose(option.id)}
      tabIndex={theme === option.id ? 0 : -1}
      className={`theme-option overflow-hidden rounded-2xl border text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${theme === option.id ? "border-accent-edge bg-accent-wash" : "border-line bg-panel hover:border-accent-edge"}`}>
      <span aria-hidden="true" className="theme-swatch flex gap-2" style={{ background: option.colors[0] }}>
        <span className="w-7 rounded-md opacity-65" style={{ background: option.colors[1] }} />
        <span className="flex flex-1 flex-col justify-center gap-2"><span className="h-2 w-2/3 rounded-full opacity-50" style={{ background: option.colors[2] }} /><span className="h-5 w-3/4 self-end rounded-lg" style={{ background: option.colors[1] }} /><span className="h-2 w-1/2 rounded-full opacity-40" style={{ background: option.colors[2] }} /></span>
      </span>
      <span className="theme-option-title flex items-center justify-between gap-2 text-sm font-medium text-ink"><span>{displayName(option)}</span><span aria-hidden="true" className={`shrink-0 text-accent-ink ${theme === option.id ? "" : "invisible"}`}>✓</span></span>
      <span className="theme-option-description block text-xs text-ink-muted">{option.description}</span>
    </button>)}
  </div>;
  return <>
    <button ref={button} type="button" onClick={toggle} aria-label={`选择界面主题，当前：${displayName(selected)}`} title="选择界面主题"
      aria-haspopup="dialog" aria-expanded={Boolean(position)} aria-controls={position ? id : undefined}
      className={`surface-button flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl text-ink-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${inline ? "" : "fixed bottom-4 left-4 z-50 bg-panel/90 depth-float sm:bottom-6 sm:left-6"}`}>
      <SunMark className="h-5 w-5" />
    </button>
    {position && createPortal(<div ref={menu} id={id} role="dialog" aria-label="界面主题" style={position}
      onPointerDown={event => event.stopPropagation()}
      onKeyDown={event => {
        if (event.key === "Escape") { event.stopPropagation(); setPosition(null); button.current?.focus(); }
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          const options = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]') ?? []);
          const current = options.indexOf(document.activeElement as HTMLButtonElement);
          const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
          options[next]?.focus();
        }
      }}
      className="fixed z-[100] w-[260px] max-h-[calc(100dvh-24px)] overflow-y-auto rounded-2xl border border-line bg-sheet p-2 depth-float">
      <p className="px-3 pb-2 pt-2 text-xs text-ink-muted">界面配色</p>
      <div role="radiogroup" aria-label="配色方案">{THEMES.map(option => <button key={option.id} type="button" role="radio" aria-checked={theme === option.id}
        onClick={() => choose(option.id)} className={`flex min-h-16 w-full items-center gap-3 rounded-xl px-3 py-2 text-left hover:bg-raised focus-visible:outline-2 focus-visible:outline-accent ${theme === option.id ? "bg-accent-wash" : ""}`}>
        <span aria-hidden="true" className="flex h-9 w-9 shrink-0 overflow-hidden rounded-full border border-line">{option.colors.map(color => <span key={color} className="flex-1" style={{ background: color }} />)}</span>
        <span className="flex-1"><span className="block text-sm font-medium text-ink">{displayName(option)}</span><span className="mt-0.5 block text-xs text-ink-muted">{option.description}</span></span>
        {theme === option.id && <span aria-hidden="true" className="text-accent-ink">✓</span>}
      </button>)}</div>
    </div>, document.body)}
  </>;
}
