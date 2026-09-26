/**
 * Browser regression for the member's one-chat workspace and admin isolation.
 * Run against a local frontend with Playwright installed / on NODE_PATH:
 *   MEMBER_SINGLE_CHAT_URL=http://127.0.0.1:3110 node tests/member-single-chat.cjs
 * All API calls are synthetic; no production data or model calls are made.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const base = process.env.MEMBER_SINGLE_CHAT_URL || "http://127.0.0.1:3110";
const artifacts = path.resolve(__dirname, "../../runtime-logs/member-single-chat");
// Match the real first turn in backend/app/opening.py for faithful UI previews.
const opening = "你好，我是一个基于行为激活（BA）的 AI 教练。接下来我会先和你一起了解近期一次具体的困扰，"
  + "观察行动如何影响情绪和状态，再逐步把这种理解落实到可尝试的改变中，并根据反馈一起调整。"
  + "我不能替代医生或心理咨询师，也不会做医学诊断。你的真实体验最重要，是否尝试、如何调整由你决定。\n"
  + "开始前，你希望我怎么称呼你？";

const profile = {
  nickname: "测试成员", tag: "12345", display_id: "测试成员12345", age: 30,
  birth_date: "1996-01-01", living_status: null, has_supporter: false,
  supporter1_relation: null, supporter1_nickname: null, supporter1_influence: null,
  supporter2_relation: null, supporter2_nickname: null, supporter2_influence: null,
  communication_preference: null, reminder_frequency: null, reminder_time_slot: null,
  physical_condition: [], behavior_taboo: [], content_taboo: [], activity_environment: null,
  activity_social: null, activity_intensity: null, supporters: [], reminder_window: null,
  preferred_provider: "deepseek", available_providers: { deepseek: true, doubao: false },
  current_module: "module_1",
};

function room(sessionId, message, module = "module_1") {
  return {
    session_id: sessionId, title: "持续对话", updated_at: "2026-09-26T10:00:00Z",
    pinned: false, revision: 1, next_module: module,
    messages: [{ role: "assistant", content: message, reasoning_content: "仅管理员可见的诊断数据" }],
  };
}

async function fixture(browser, { role = "user", width = 1280, failed = false, module = "module_1" } = {}) {
  const page = await browser.newPage({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
  const requests = [];
  const errors = [];
  const createdSessionIds = [];
  let detail = room("member-current", role === "admin" ? "让我们回顾这次已经完成的活动。" : opening, module);
  if (role === "admin") detail.messages.push({ role: "user", content: "这是已经开始的管理员对话。" });
  const rooms = new Map([[detail.session_id, detail]]);
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem("psy-auth-token", "synthetic-member"));
  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== new URL(base).origin) return route.abort();
    if (!url.pathname.startsWith("/api/")) return route.continue();
    requests.push({ path: url.pathname, method: request.method(), body: request.postData() });
    const json = (data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    if (url.pathname === "/api/auth/me") return json({
      username: "fixture", nickname: "测试成员", tag: "12345", display_id: "测试成员12345",
      role, profile_uuid: "fixture", current_module: module,
      email_required: false, email_verified: true, birth_date_required: false,
    });
    if (url.pathname === "/api/conversations/current") {
      assert.equal(request.method(), "POST");
      return failed ? json({ detail: "synthetic outage" }, 503) : json(detail);
    }
    if (url.pathname === "/api/conversations") {
      if (request.method() === "POST") {
        const sessionId = `admin-new-${createdSessionIds.length + 1}`;
        createdSessionIds.push(sessionId);
        detail = room(sessionId, opening, "module_1");
        detail.title = `新对话 ${createdSessionIds.length}`;
        rooms.set(sessionId, detail);
        return json(detail);
      }
      return json([...rooms.values()]);
    }
    if (url.pathname.endsWith("/revision")) return json({ revision: detail.revision });
    if (url.pathname.endsWith("/events")) return route.fulfill({ contentType: "text/event-stream", body: "" });
    if (url.pathname.startsWith("/api/conversations/")) return json(rooms.get(url.pathname.split("/").pop()) || detail);
    if (url.pathname === "/api/chat/stream") {
      const payload = request.postDataJSON();
      assert.equal(payload.session_id, detail.session_id, "turn must remain in the current chat");
      detail.messages.push({ role: "user", content: payload.message }, { role: "assistant", content: "我们继续这段对话。" });
      detail.revision += 2;
      const event = (name, data) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
      return route.fulfill({ contentType: "text/event-stream", body:
        event("meta", { session_id: detail.session_id }) +
        event("delta", { text: "我们继续这段对话。" }) + event("persisted", { saved: true }) });
    }
    if (url.pathname === "/api/profile") return json({ ...profile, current_module: module });
    if (url.pathname === "/api/assessment/history") return json({ items: [], has_more: false, next_offset: null });
    return json({});
  });
  return { page, requests, errors, createdSessionIds };
}

async function assertMemberControls(page) {
  assert.equal(await page.getByRole("navigation").count(), 0, "members must not receive a mounted sidebar");
  assert.equal(await page.getByRole("button", { name: "切换对话侧栏", exact: true }).count(), 0);
  for (const name of ["开启新对话", "开启新目标", "我的目标", "调试详情", "账号与权限管理", "全局提示词管理", "问题反馈管理", "每日记录数据"]) {
    assert.equal(await page.getByText(name, { exact: true }).count(), 0, `member UI leaked ${name}`);
  }
  for (const name of ["每日记录", "我的档案"]) {
    const button = page.getByRole("button", { name, exact: true });
    assert.equal(await button.count(), 1);
    const box = await button.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= page.viewportSize().width && box.height >= 44,
      `${name} must be visible, in viewport, and touch-sized: ${JSON.stringify(box)}`);
  }
  const dimensions = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth }));
  assert.ok(dimensions.content <= dimensions.width, "member workspace must not scroll horizontally");
}

async function checkMember(browser, width) {
  const { page, requests, errors } = await fixture(browser, { width, module: width === 390 ? "module_3" : "module_1" });
  try {
    await page.goto(base);
    await page.getByText("开始前，你希望我怎么称呼你？", { exact: false }).waitFor();
    await assertMemberControls(page);
    assert.ok(requests.some(r => r.path === "/api/conversations/current"));
    await page.screenshot({ path: path.join(artifacts, `member-${width}.png`), fullPage: true });

    await page.getByRole("button", { name: "每日记录", exact: true }).click();
    const daily = page.getByRole("dialog", { name: "今天的行为记录" });
    await daily.waitFor();
    await daily.getByRole("button", { name: "关闭", exact: true }).click();
    await daily.waitFor({ state: "detached" });
    await page.getByRole("button", { name: "我的档案", exact: true }).click();
    const personal = page.getByRole("dialog", { name: "我的档案", exact: true });
    await personal.getByLabel("公开昵称", { exact: true }).waitFor();
    assert.equal(await personal.getByLabel("公开昵称", { exact: true }).inputValue(), "测试成员");
    await personal.getByRole("button", { name: "关闭", exact: true }).click();

    await page.getByRole("button", { name: /账号菜单/ }).click();
    for (const text of ["管理员控制台", "账号与权限管理", "全局提示词管理", "问题反馈管理", "每日记录数据"]) {
      assert.equal(await page.getByText(text, { exact: true }).count(), 0, `member menu leaked ${text}`);
    }
    await page.getByRole("button", { name: /账号菜单/ }).click();
    const input = page.getByLabel("Message", { exact: true });
    await input.fill("继续说说刚才的话题");
    await input.press("Enter");
    await page.getByText("我们继续这段对话。", { exact: true }).waitFor();
    await page.reload();
    await page.getByText("继续说说刚才的话题", { exact: true }).waitFor();
    await page.getByText("我们继续这段对话。", { exact: true }).waitFor();
    await assertMemberControls(page);
    assert.equal(requests.filter(r => r.path === "/api/conversations" && r.method === "POST").length, 0,
      "member mount/reload/send must never call the administrative create endpoint");
    assert.equal(requests.filter(r => r.path.startsWith("/api/admin/")).length, 0);
    assert.deepEqual(errors, []);
    console.log(`PASS: member ${width}px personal controls, isolated UI, send and resumed history`);
  } finally { await page.close(); }
}

(async () => {
  await fs.mkdir(artifacts, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "msedge" });
  try {
    await checkMember(browser, 1280);
    await checkMember(browser, 390);

    const narrow = await fixture(browser, { width: 320 });
    await narrow.page.goto(base);
    await narrow.page.getByText("开始前，你希望我怎么称呼你？", { exact: false }).waitFor();
    await assertMemberControls(narrow.page);
    await narrow.page.screenshot({ path: path.join(artifacts, "member-320.png"), fullPage: true });
    assert.deepEqual(narrow.errors, []);
    await narrow.page.close();
    console.log("PASS: 320px member header remains usable without horizontal overflow");

    const failed = await fixture(browser, { failed: true });
    await failed.page.goto(base);
    await failed.page.getByText(/Loading your conversation failed \(503\)/).waitFor();
    assert.ok(failed.requests.some(r => r.path === "/api/conversations/current"));
    assert.equal(failed.requests.filter(r => r.path === "/api/conversations" && r.method === "POST").length, 0);
    assert.deepEqual(failed.errors, []);
    await failed.page.close();
    console.log("PASS: first-load failure is surfaced without creating a replacement conversation");

    const deepLink = await fixture(browser);
    await deepLink.page.goto(`${base}/?pa_reminder=1`);
    await deepLink.page.getByRole("dialog", { name: "今天的行为记录" }).waitFor();
    assert.equal(await deepLink.page.getByRole("dialog", { name: "我的目标", exact: true }).count(), 0);
    assert.deepEqual(deepLink.errors, []);
    await deepLink.page.close();
    console.log("PASS: member notification link opens the daily record directly");

    const admin = await fixture(browser, { role: "admin", module: "module_4" });
    await admin.page.goto(base);
    await admin.page.getByText("当前 MODULE IV", { exact: true }).waitFor();
    await admin.page.getByRole("button", { name: "调试详情", exact: true }).waitFor();
    await admin.page.getByRole("navigation", { name: "对话历史" }).waitFor();
    assert.equal(await admin.page.getByRole("button", { name: "切换对话侧栏", exact: true }).count(), 1);
    // First start abandons the M4 view; the second starts again even when the
    // freshly created M1 chat has no user turn yet. Each must receive a new id.
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      const created = admin.page.waitForResponse(response => new URL(response.url()).pathname === "/api/conversations" && response.request().method() === "POST");
      await admin.page.getByRole("button", { name: "开启新对话", exact: true }).click();
      const response = await (await created).json();
      assert.equal(response.session_id, `admin-new-${attempt}`);
      assert.equal(response.next_module, "module_1");
      assert.deepEqual(response.messages.map(message => message.content), [opening]);
      await admin.page.getByText("当前 MODULE I", { exact: true }).waitFor();
      await admin.page.getByText("开始前，你希望我怎么称呼你？", { exact: false }).waitFor();
      assert.equal(await admin.page.getByText("这是已经开始的管理员对话。", { exact: true }).count(), 0);
      assert.equal(await admin.page.getByRole("dialog").count(), 0, "New Conversation must never mount a goal chooser");
      assert.equal(await admin.page.getByText("这段对话从哪里开始？", { exact: true }).count(), 0);
    }
    assert.equal(new Set(admin.createdSessionIds).size, 2, "even an untouched fresh chat must not be reused");
    assert.equal(admin.requests.filter(r => r.path === "/api/conversations/current").length, 0);
    assert.equal(admin.requests.filter(r => r.path === "/api/conversations" && r.method === "POST").length, 2);
    assert.equal(admin.requests.filter(r => r.path.startsWith("/api/program/")).length, 0,
      "starting a fresh chat must not load or select an existing program goal");
    await admin.page.getByRole("button", { name: /账号菜单/ }).click();
    await admin.page.getByText("管理员控制台", { exact: true }).waitFor();
    await admin.page.getByText("账号与权限管理", { exact: true }).waitFor();
    await admin.page.getByText("全局提示词管理", { exact: true }).waitFor();
    await admin.page.screenshot({ path: path.join(artifacts, "admin-1280.png"), fullPage: true });
    assert.deepEqual(admin.errors, []);
    await admin.page.close();
    console.log("PASS: admin M4 to new M1, repeated fresh M1 opening, no goal chooser, diagnostics and management menu");
    console.log(`Screenshots: ${artifacts}`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
