import type { Metadata, Viewport } from "next";
import { DEFAULT_THEME, THEME_KEY, THEME_META_COLOR } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: "BA行为激活教练 · BA Coach",
  description: "Psychology AI workflow, migrated from Coze to a standalone app.",
  appleWebApp: { capable: true, title: "BA Coach", statusBarStyle: "default" },
  icons: { apple: "/manifest-icon/180" },
};

export const viewport: Viewport = {
  themeColor: THEME_META_COLOR[DEFAULT_THEME],
};

/**
 * Runs before paint to restore an explicit choice without a theme flash.
 * Missing, invalid, or unavailable storage retains the server's light default.
 * It has to be a blocking inline script —
 * anything deferred to React would land a paint too late, which is the flash
 * every theme switcher is trying to avoid.
 */
const themeScript = `try{var dark=localStorage.getItem(${JSON.stringify(
  THEME_KEY,
)})==="dark";document.documentElement.classList.toggle("theme-warm",!dark);var meta=document.querySelector('meta[name="theme-color"]');if(meta)meta.setAttribute("content",dark?${JSON.stringify(THEME_META_COLOR.dark)}:${JSON.stringify(THEME_META_COLOR.warm)})}catch(e){}`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // The script above edits <html>'s class list before hydration, which React
    // would otherwise report as a server/client mismatch.
    <html lang="zh-CN" className="theme-warm" suppressHydrationWarning>
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
