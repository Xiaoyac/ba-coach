import type { Metadata, Viewport } from "next";
import { THEME_KEY, THEME_META_COLOR } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: "BA行为激活教练 · BA Coach",
  description: "Psychology AI workflow, migrated from Coze to a standalone app.",
};

export const viewport: Viewport = {
  // Dark Zen is the default; ThemeToggle rewrites this tag when it switches.
  themeColor: THEME_META_COLOR.dark,
};

/**
 * Runs before the browser paints anything, so a returning warm-theme user
 * never sees a frame of charcoal. It has to be a blocking inline script —
 * anything deferred to React would land a paint too late, which is the flash
 * every theme switcher is trying to avoid.
 */
const themeScript = `try{if(localStorage.getItem(${JSON.stringify(
  THEME_KEY,
)})==="warm")document.documentElement.classList.add("theme-warm")}catch(e){}`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // The script above edits <html>'s class list before hydration, which React
    // would otherwise report as a server/client mismatch.
    <html lang="zh-CN" suppressHydrationWarning>
      {/* Browser extensions (e.g. Monica) inject their own attributes into
          <body> — monica-id, monica-version — before React hydrates. That's
          a mismatch React can't do anything about and isn't this app's bug;
          suppress the warning here rather than let it surface as a visible
          error to every user who happens to have that extension installed. */}
      <body suppressHydrationWarning>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        {children}
      </body>
    </html>
  );
}
