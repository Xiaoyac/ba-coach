/**
 * Regression: an in-flight turn belongs to its conversation, not to the
 * currently visible room.  Switching from A to B while A is still waiting
 * must not show A's typing indicator in B; returning to A must reconcile the
 * durable assistant reply.
 *
 * Run against a local frontend, for example:
 *   node tests/chat-generation-isolation.cjs
 * (override CHAT_GENERATION_ISOLATION_URL when the dev server uses another
 * loopback port.)
 */
const assert = require("node:assert/strict");
const { chromium } = require("playwright");

const base = process.env.CHAT_GENERATION_ISOLATION_URL || "http://127.0.0.1:3108";

const rooms = {
  A: {
    session_id: "room-a",
    title: "正在写作的对话",
    updated_at: "2026-09-19T10:00:00.000Z",
    pinned: false,
    revision: 1,
    messages: [{ role: "assistant", content: "这是 A 对话。" }],
    next_module: "module_1",
  },
  B: {
    session_id: "room-b",
    title: "另一条对话",
    updated_at: "2026-09-19T09:00:00.000Z",
    pinned: false,
    revision: 1,
    messages: [{ role: "assistant", content: "这是 B 对话。" }],
    next_module: "module_1",
  },
};
let streamCount = 0;

function summary(room) {
  return {
    session_id: room.session_id,
    title: room.title,
    updated_at: room.updated_at,
    pinned: room.pinned,
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: "msedge" });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.addInitScript(() => localStorage.setItem("psy-auth-token", "synthetic-isolation"));
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "127.0.0.1") return route.abort();
    if (!url.pathname.startsWith("/api/")) return route.continue();

    const json = (data, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(data),
    });
    const detailFor = (id) => rooms[id === "room-b" ? "B" : "A"];

    if (url.pathname === "/api/auth/me") {
      return json({ username: "fixture", nickname: "测试用户", role: "user", profile_uuid: "fixture",
        email_required: false, email_verified: true });
    }
    if (url.pathname === "/api/conversations" && request.method() === "GET") {
      return json([summary(rooms.A), summary(rooms.B)]);
    }
    if (url.pathname.startsWith("/api/conversations/") && url.pathname.endsWith("/revision")) {
      const id = url.pathname.includes("room-b") ? "B" : "A";
      return json({ revision: rooms[id].revision });
    }
    if (url.pathname.startsWith("/api/conversations/") && url.pathname.endsWith("/events")) {
      // Keep the live-sync request open, as the production event stream does.
      return route.fulfill({ contentType: "text/event-stream", body: "" });
    }
    if (url.pathname.startsWith("/api/conversations/")) {
      const id = url.pathname.endsWith("room-b") ? "B" : "A";
      return json(rooms[id]);
    }
    if (url.pathname === "/api/chat/stream" && request.method() === "POST") {
      const payload = request.postDataJSON();
      assert.equal(payload.session_id, "room-a");
      streamCount += 1;

      if (streamCount === 2) {
        // Commit the reply while deliberately keeping the POST response open.
        // The revision poll must adopt this durable transcript and clear the
        // spinner before the stream eventually emits anything.
        await sleep(2200);
        rooms.A.messages = [
          ...rooms.A.messages,
          { role: "user", content: payload.message },
          { role: "assistant", content: "A 的轮询回复已经保存。" },
        ];
        rooms.A.revision += 2;
        await sleep(5000);
        const event = (name, data) => `event: ${name}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
        try {
          return route.fulfill({
            contentType: "text/event-stream",
            body: event("meta", { session_id: "room-a" })
              + event("delta", { text: "A 的轮询回复已经保存。" })
              + event("persisted", { saved: true }),
          });
        } catch {
          // The revision reconciler aborts this intentionally orphaned stream.
          return;
        }
      }

      // Leave A pending long enough for the test to switch to B.  The server
      // persists the turn before the stream is released, matching the real
      // durable-reply/recovery path.
      await sleep(700);
      rooms.A.messages = [
        ...rooms.A.messages,
        { role: "user", content: payload.message },
        { role: "assistant", content: "A 的回复已经保存。" },
      ];
      rooms.A.revision += 2;
      const event = (name, data) => `event: ${name}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
      return route.fulfill({
        contentType: "text/event-stream",
        body: event("meta", { session_id: "room-a" })
          + event("delta", { text: "A 的回复已经保存。" })
          + event("persisted", { saved: true }),
      });
    }
    if (url.pathname === "/api/assessment/history") return json({ items: [], has_more: false, next_offset: null });
    return json({});
  });

  try {
    await page.goto(base);
    await page.getByText("这是 A 对话。", { exact: true }).waitFor();

    const input = page.getByLabel("Message", { exact: true });
    await input.fill("请回复 A");
    await input.press("Enter");
    await page.getByLabel("Thinking").waitFor();

    // Move to B before A's delayed stream returns.  B must be idle: no A
    // typing indicator and no disabled/waiting composer state.
    // The row's accessible name also contains its relative timestamp, so
    // target the exact title text rather than the whole button name.
    await page.getByText("另一条对话", { exact: true }).click();
    await page.getByText("这是 B 对话。", { exact: true }).waitFor();
    assert.equal(await page.locator('[data-generation-status]').count(), 0,
      "the hidden room's typing status leaked into the visible room");
    assert.equal(await page.getByLabel("Send", { exact: true }).count(), 1,
      "the visible room should not remain in the hidden room's busy state");

    // Return to A after the delayed stream persists.  The durable assistant
    // message must be present even if the stream's final event was missed.
    await sleep(900);
    await page.getByText("正在写作的对话", { exact: true }).click();
    await page.getByText("A 的回复已经保存。", { exact: true }).waitFor();
    assert.equal(await page.locator('[data-generation-status]').count(), 0);

    // Second turn: durable reply arrives before the stream does. This is the
    // production failure mode behind an endless spinner on mobile/proxy
    // connections, and proves revision polling is allowed during generation.
    await input.fill("请用 revision poll 回复");
    await input.press("Enter");
    await page.getByLabel("Thinking").waitFor();
    await page.getByText("A 的轮询回复已经保存。", { exact: true }).waitFor({ timeout: 12000 });
    assert.equal(await page.locator('[data-generation-status]').count(), 0,
      "revision reconciliation should end the spinner before stream EOF");
    assert.deepEqual(errors, []);
    console.log("PASS: per-conversation generation isolation and durable reply recovery");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
