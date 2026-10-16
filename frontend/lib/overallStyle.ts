import { DEFAULT_THEME, THEMES, THEME_META_COLOR } from "@/lib/theme";

export type OverallStyle = "default" | "simple" | "participant-1" | "claude";
const LAYERED: readonly OverallStyle[] = ["simple", "participant-1", "claude"];
const isLayered = (value: unknown): value is OverallStyle => LAYERED.includes(value as OverallStyle);
type StyleAccount = { profile_uuid: string; role: string } | null;
export const STYLE_KEY_PREFIX = "bacoach-overall-style:";
let account: StyleAccount = null;
let active: OverallStyle = "default";
const listeners = new Set<() => void>();
const memory = new Map<string, OverallStyle>();

export function getOverallStyle(): OverallStyle { return active; }
export function subscribeOverallStyle(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function apply(style: OverallStyle) {
  active = style;
  if (typeof document !== "undefined") {
    document.documentElement.classList.toggle("style-simple", style !== "default");
    document.documentElement.classList.toggle("style-participant-1", style === "participant-1");
    document.documentElement.classList.toggle("style-claude", style === "claude");
    const theme = THEMES.find(item => document.documentElement.classList.contains(`theme-${item.id}`))?.id ?? DEFAULT_THEME;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content",
      theme === "warm" && style !== "default" ? "#f8f7f4" : THEME_META_COLOR[theme]);
  }
  listeners.forEach(listener => listener());
}

function restore() {
  // Public account entry always uses simple; this does not save a preference.
  // Verified members still use default, and admins restore their own choice.
  if (!account) { apply("simple"); return; }
  if (account?.role !== "admin") { apply("default"); return; }
  const key = STYLE_KEY_PREFIX + account.profile_uuid;
  let value: unknown;
  try { value = localStorage.getItem(key); }
  catch { value = memory.get(key); }
  apply(isLayered(value) ? value : "default");
}

/** Called only with the identity verified by AuthGate, never a cached role. */
export function bindOverallStyleAccount(next: StyleAccount) {
  account = next;
  restore();
  const stored = (event: StorageEvent) => {
    if (event.key === null || event.key === STYLE_KEY_PREFIX + account?.profile_uuid) restore();
  };
  const themeChanged = () => apply(active);
  window.addEventListener("storage", stored);
  window.addEventListener("themechange", themeChanged);
  return () => {
    window.removeEventListener("storage", stored);
    window.removeEventListener("themechange", themeChanged);
    account = null;
    apply("default");
  };
}

export function chooseOverallStyle(style: OverallStyle) {
  if (account?.role !== "admin") { restore(); return; }
  const safe = isLayered(style) ? style : "default";
  const key = STYLE_KEY_PREFIX + account.profile_uuid;
  memory.set(key, safe);
  try { localStorage.setItem(key, safe); } catch { /* Still usable without browser storage. */ }
  apply(safe);
}
