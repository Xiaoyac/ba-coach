/**
 * Shared request headers for every API client in `lib/` (chat, assessment,
 * conversations). Pulled out once three call sites needed the same fix at
 * the same time, rather than left triplicated.
 */

import { getToken } from "@/lib/auth";

export function checkAuthentication(res: Response): void {
  if (res.status === 401) {
    window.dispatchEvent(new Event("bacoach-auth-expired"));
    throw new Error("登录已失效，请重新登录；已保存的记录不会丢失。");
  }
}

export function apiHeaders(opts: { json?: boolean } = {}): HeadersInit {
  const token = getToken();
  return {
    ...(opts.json ? { "Content-Type": "application/json" } : {}),
    // Identity is now a credential the server verifies, not an id the client
    // asserts — see lib/auth.ts. Omitted entirely when signed out: chat still
    // answers without one (it just persists nothing), and everything else
    // correctly 401s rather than being handed a forgeable header.
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    // ngrok's free tier serves an HTML "you are about to visit…" interstitial
    // to any request that doesn't carry this header, instead of proxying
    // through to the app. Without it, every fetch from a browser that hasn't
    // already clicked through that page once fails — a JSON parse error at
    // best, indistinguishable from a dead backend at worst. Harmless to send
    // when not running behind ngrok; the backend never looks at it.
    "ngrok-skip-browser-warning": "true",
  };
}
