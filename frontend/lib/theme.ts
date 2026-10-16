/**
 * Theme constants shared by the client-side toggle and the pre-hydration
 * script that app/layout.tsx inlines.
 *
 * This has to be a plain module rather than living in ThemeToggle.tsx: the
 * layout is a Server Component, and a value imported from a `"use client"`
 * file arrives there as a client *reference*, not the value. Interpolating one
 * into the script string silently produced `localStorage.getItem(undefined)`,
 * so the stored theme never restored on reload.
 */

export type Theme = "dark" | "warm" | "porcelain" | "github";

export const THEME_KEY = "psy-theme";
export const DEFAULT_THEME: Theme = "warm";

/** Keeps mobile browser chrome (address bar, status bar) matching the app. */
export const THEME_META_COLOR: Record<Theme, string> = {
  dark: "#010a13",
  warm: "#f2eee5",
  porcelain: "#f2f6f8",
  github: "#1e1f22",
};

export const THEMES: { id: Theme; name: string; description: string; colors: string[] }[] = [
  { id: "warm", name: "温暖浅色", description: "奶油白 · 温润木色", colors: ["#fdfcf9", "#eeeae1", "#80613e"] },
  { id: "porcelain", name: "青花瓷", description: "瓷白 · 淡青蓝", colors: ["#fcfdfc", "#e4edf5", "#37658a"] },
  // Preserve saved selections while using the Discord-inspired cool gray / blurple palette.
  { id: "github", name: "Dark", description: "冷灰 · 蓝紫", colors: ["#2b2d31", "#3b4274", "#5865f2"] },
  { id: "dark", name: "黑金夜色", description: "深海蓝 · 古典金", colors: ["#010a13", "#102635", "#c8aa6e"] },
];

export function isTheme(value: unknown): value is Theme {
  return THEMES.some(theme => theme.id === value);
}
