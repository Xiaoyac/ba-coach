"use client";

import { useEffect, useState } from "react";
import { MoonMark, SunMark } from "@/components/icons";
import { THEME_KEY, THEME_META_COLOR, type Theme } from "@/lib/theme";

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("theme-warm", theme === "warm");
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", THEME_META_COLOR[theme]);
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("dark");

  // The blocking script in <body> has already set the class before paint, so
  // read the answer back off the DOM rather than off localStorage again. This
  // is also why the initial state is a hard-coded "dark" — it has to match
  // what the server rendered or React complains about the mismatch.
  useEffect(() => {
    const active: Theme = document.documentElement.classList.contains(
      "theme-warm",
    )
      ? "warm"
      : "dark";
    setTheme(active);
    // Re-apply rather than just record: the inline script sets the class but
    // not <meta name="theme-color">, which is still rendered with the dark
    // default, so a warm-theme reload would leave the browser chrome charcoal.
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
      title={isWarm ? "Dark Zen" : "Warm Therapeutic"}
      className="group fixed bottom-4 left-4 z-50 flex h-10 w-10 items-center justify-center rounded-full border border-line bg-panel text-ink-muted depth-float backdrop-blur-xl transition-colors duration-500 hover:border-accent-edge hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:bottom-6 sm:left-6"
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
