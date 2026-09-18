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

export type Theme = "dark" | "warm";

export const THEME_KEY = "psy-theme";
export const DEFAULT_THEME: Theme = "warm";

/** Keeps mobile browser chrome (address bar, status bar) matching the app. */
export const THEME_META_COLOR: Record<Theme, string> = {
  dark: "#010a13",
  warm: "#f2eee5",
};
