"use client";

import { useEffect, useState } from "react";
import { MoonMark, SunMark } from "@/components/icons";
import { DEFAULT_THEME, THEME_KEY, THEME_META_COLOR, type Theme } from "@/lib/theme";

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("theme-warm", theme === "warm");
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", THEME_META_COLOR[theme]);
}

export default function ThemeToggle({ inline = false }: { inline?: boolean }) {
  const [theme, setTheme] = useState<Theme>(DEFAULT_THEME);

  // The blocking script in <body> has already set the class before paint, so
  // read the answer back off the DOM rather than off localStorage again. This
  // is also why the initial state uses the shared default — it has to match
  // what the server rendered or React complains about the mismatch.
  useEffect(() => {
    const active: Theme = document.documentElement.classList.contains(
      "theme-warm",
    )
      ? "warm"
      : "dark";
    setTheme(active);
    // Keep the browser chrome synchronized with the restored theme.
    applyTheme(active);
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "warm" : "dark";
    setTheme(next);
    applyTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // Private browsing / storage disabled. The theme still switches for
      // this session; it just won't be remembered.
    }
  }

  const isWarm = theme === "warm";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={isWarm ? "切换到暗色主题" : "切换到暖色主题"}
      title={isWarm ? "切换到黑金夜色" : "切换到温暖浅色"}
      className={`surface-button group flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl text-ink-muted transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${inline ? "" : "fixed bottom-4 left-4 z-50 bg-panel/90 depth-float sm:bottom-6 sm:left-6"}`}
    >
      {/* Both glyphs stay mounted and cross-fade, so the switch is a dissolve
          rather than a swap between two different shapes. */}
      <span className="relative flex h-[18px] w-[18px] items-center justify-center">
        <SunMark
          className={`absolute h-[18px] w-[18px] transition-all duration-500 ease-out ${
            isWarm ? "scale-75 opacity-0" : "scale-100 opacity-100"
          }`}
        />
        <MoonMark
          className={`absolute h-[18px] w-[18px] transition-all duration-500 ease-out ${
            isWarm ? "scale-100 opacity-100" : "scale-75 opacity-0"
          }`}
        />
      </span>
    </button>
  );
}
