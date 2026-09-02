/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Hides the floating Next.js "N" badge in the bottom-left corner during
  // `next dev`. It is dev-only tooling and never ships, but it sits exactly
  // where the theme toggle lives.
  devIndicators: false,
  // Next's built-in gzip middleware buffers a response to compute its
  // compressed size before sending anything — fine for a normal page, fatal
  // for the proxied /api/chat/stream SSE below: the whole reply sits in the
  // buffer until the model finishes, then lands in the browser as one
  // chunk instead of live deltas. Confirmed with the rewrite in place: a
  // ~10s generation produced a single `content-encoding: gzip` chunk at the
  // 10s mark, not a stream. Over real network latency (ngrok, another
  // device) that delay reads as "the agent isn't replying" and invites
  // retries, each minting a fresh session since the first one's session_id
  // never made it back. SSE payloads don't compress well anyway (mostly
  // already-short text deltas), so disabling this costs little.
  compress: false,
  // Proxies /api/* to the backend through this (Node) server instead of
  // making the browser call the backend directly. That turns "frontend and
  // backend on two different origins" into a server-to-server detail the
  // browser never sees: no CORS, no NEXT_PUBLIC_API_BASE_URL to configure,
  // and exposing the app over ngrok only needs one tunnel (the frontend's) —
  // the backend can stay on localhost since this proxy runs on the same
  // machine as the backend, not in the visitor's browser.
  //
  // BACKEND_ORIGIN is a plain server-side env var (no NEXT_PUBLIC_ prefix —
  // it must never reach the client bundle) and, like next.config.mjs itself,
  // is only read when the Next.js server starts; changing it needs a
  // restart, not just a reload.
  async rewrites() {
    const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";
    return [
      { source: "/api/:path*", destination: `${backendOrigin}/api/:path*` },
    ];
  },
};

export default nextConfig;
