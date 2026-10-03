// All backend calls are synthetic. Network backstop forbids real API writes.
// NODE_PATH=<playwright modules> node infra/qa/goal-formulation-1003.cjs [baseUrl]
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const OUT = path.resolve(__dirname, '../../.test-tmp/goal-card-ui/browser');

function installFixtures() {
  localStorage.setItem('psy-auth-token', 'synthetic-goal-card-1003');
  localStorage.setItem('psy-theme', 'warm');
  const f = window.goalCardFixture = {
    card: JSON.parse(sessionStorage.getItem('qa-goal-restore-card') || 'null'), enabled: true, archive: [], module: 'module_2', revision: 1, messages: [{ id: 10, role: 'assistant', content: '先聊聊你想做些什么。' }],
    calls: [], writes: [], chats: [], unknown: [], failPut: false, failConfirm: false, holdCard: false, held: [],
  };
  const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
  const detail = () => ({ session_id: 'goal-card-ui', title: '目标卡验收', revision: f.revision, updated_at: '2026-10-03T00:00:00Z', next_module: f.module, pinned: false, messages: f.messages });
  const events = new Set();
  f.broadcast = () => { f.revision++; for (const controller of events) controller.enqueue(new TextEncoder().encode(`event: snapshot\ndata: ${JSON.stringify(detail())}\n\n`)); window.dispatchEvent(new Event('focus')); };
  f.setCard = (kind, id) => { f.card = { id, kind, phase: 'formulating', revision: 1, fields: {}, concerns: [] }; f.broadcast(); };
  f.release = () => { f.holdCard = false; for (const resolve of f.held.splice(0)) resolve(); };
  const original = window.fetch.bind(window);
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const p = url.pathname;
    if (!p.startsWith('/api/')) return original(input, options);
    const method = options.method || 'GET'; f.calls.push({ path: p, method });
    if (p === '/api/auth/me') return json({ username: 'goal-test', nickname: '普通用户', role: 'user', profile_uuid: 'goal-test', email_required: false, email_verified: true, birth_date_required: false });
    if (p === '/api/modules') return json({ modules: ['module_1', 'module_2', 'module_3', 'module_4'] });
    if (p === '/api/message-feedback') return json([]);
    if (p === '/api/conversations/goal-card-ui/context') return json({ estimated_tokens: 20, token_budget: null, retained_messages: f.messages.length, summarized_messages: 0, total_messages: f.messages.length, message_budget: null, mode: 'compression' });
    if (p === '/api/conversations') return json(method === 'POST' ? detail() : [detail()]);
    if (p === '/api/conversations/current' || p === '/api/conversations/goal-card-ui') return json(detail());
    if (p.endsWith('/revision')) return json({ revision: f.revision });
    if (p.endsWith('/events')) { let c; return new Response(new ReadableStream({ start(controller) { c = controller; events.add(c); }, cancel() { events.delete(c); } }), { headers: { 'Content-Type': 'text/event-stream' } }); }
    if (p === '/api/assessment/by-date') return json(null);
    if (p === '/api/program/goals/overview') return json({ enabled: true, goals: [], pa_cards: [], formulation_cards: f.archive });
    if (p === '/api/program/goal-card-ui') return json({ enabled: true, runtime: { active_goal_id: null, row_version: 1 } });
    if (p === '/api/program/goal-card-ui/goal-card') {
      if (method === 'GET') { const value = structuredClone({ enabled: f.enabled, card: f.card }); if (f.holdCard) await new Promise(resolve => f.held.push(resolve)); return json(value); }
      const body = JSON.parse(options.body); f.writes.push(body);
      if (f.failPut || body.revision !== f.card?.revision || body.card_id !== f.card?.id) return json({ detail: '目标卡版本已改变' }, 409);
      f.card = { ...f.card, fields: body.fields, phase: 'discussing', revision: f.card.revision + 1 };
      return json({ enabled: true, card: f.card, submission_text: '我在目标卡上填了这些想法，请一起讨论。' });
    }
    if (p === '/api/chat/stream' && method === 'POST') {
      const body = JSON.parse(options.body); f.chats.push(body);
      if (body.metadata?.goal_card_action === 'confirm' && f.failConfirm) return json({ detail: '旧版本不能确认' }, 409);
      const action = body.metadata?.goal_card_action;
      if (!action && body.message.includes('继续完善之前暂存')) f.card = { ...f.card, phase: 'formulating', revision: f.card.revision + 1 };
      if (action === 'submit') f.card = { ...f.card, phase: 'ready', revision: f.card.revision + 1 };
      if (action === 'pause') f.card = { ...f.card, phase: 'paused', revision: f.card.revision + 1 };
      if (action === 'confirm') { f.card = { ...f.card, phase: 'confirmed', revision: f.card.revision + 1, confirmed_at: '2026-10-03T00:00:00Z' }; f.archive.push(structuredClone(f.card)); }
      const userId = 11 + f.messages.length;
      f.messages.push({ id: userId, role: 'user', content: body.message }, { id: userId + 1, reply_to_message_id: userId, role: 'assistant', content: action === 'confirm' ? '已确认，这个安排保存好了。' : action === 'pause' ? '好，先保留这些想法。' : '我看过你的安排了，你可以确认，也可以继续调整。' });
      f.revision++;
      const frames = [['meta', { session_id: 'goal-card-ui', user_message_id: userId, reply_module: 'module_2', next_module: 'module_2' }], ['delta', { text: f.messages.at(-1).content }], ['persisted', { saved: true }]];
      return new Response(frames.map(([event, value]) => `event: ${event}\ndata: ${JSON.stringify(value)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (p.startsWith('/api/push/')) return json({ enabled: false, available: false });
    f.unknown.push({ path: p, method }); return json({ detail: `Unmocked ${method} ${p}` }, 501);
  };
}

async function noOverflow(page) {
  assert.deepEqual(await page.evaluate(() => {
    const issues = [];
    if (document.documentElement.scrollWidth > innerWidth + 1) issues.push('page horizontal overflow');
    for (const element of document.querySelectorAll('[aria-label="目标卡快捷入口"], dialog[open]')) {
      const b = element.getBoundingClientRect();
      if (b.left < -1 || b.right > innerWidth + 1) issues.push('panel outside width');
    }
    const composer = document.querySelector('textarea[aria-label="Message"]')?.getBoundingClientRect();
    if (composer && composer.bottom > innerHeight + 1) issues.push('composer offscreen');
    return issues;
  }), []);
}

async function waitFor(page, expression) { await page.waitForFunction(expression); }

exports.run = async (browser, baseUrl = 'http://127.0.0.1:3123') => {
  await fs.mkdir(OUT, { recursive: true });
  const results = [];
  for (const width of [1440, 390, 360]) {
    const context = await browser.newContext({ viewport: { width, height: width < 500 ? 844 : 900 }, reducedMotion: 'reduce', isMobile: width < 500, hasTouch: width < 500 });
    await context.addInitScript(installFixtures);
    const page = await context.newPage();
    const errors = [], escaped = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', route => { escaped.push(route.request().url()); return route.fulfill({ status: 599, body: 'Real API forbidden' }); });
    try {
      await page.goto(baseUrl); const panel = page.getByRole('region', { name: '目标卡快捷入口', exact: true });
      await panel.getByText('先聊聊你的想法，开始制定目标时，卡片会在这里展开。', { exact: true }).waitFor();
      assert.equal(await panel.getByLabel('想做的活动', { exact: true }).count(), 0, 'M2 entry alone does not open a card');
      await page.evaluate(() => window.goalCardFixture.setCard('primary', 'primary-one'));
      const activity = panel.getByLabel('想做的活动', { exact: true }); await activity.waitFor();
      assert.equal(await activity.inputValue(), '');
      assert.equal(await panel.getByLabel('你觉得有多难', { exact: true }).inputValue(), '', 'no default difficulty');
      await noOverflow(page);
      await page.screenshot({ path: path.join(OUT, `primary-open-${width}.png`), fullPage: true, animations: 'disabled' });
      await activity.fill('晚饭后散步');
      await page.evaluate(() => { window.goalCardFixture.holdCard = true; window.dispatchEvent(new Event('focus')); });
      await waitFor(page, () => window.goalCardFixture.held.length > 0);
      await activity.fill('晚饭后散步五分钟');
      await page.evaluate(() => window.goalCardFixture.release());
      assert.equal(await activity.inputValue(), '晚饭后散步五分钟', 'late reads cannot replace typed draft');
      await page.evaluate(() => { window.goalCardFixture.card.fields.schedule_text = '明天下午'; window.goalCardFixture.card.revision++; window.dispatchEvent(new Event('focus')); });
      await panel.getByText('教练更新了卡片。你正在填写的内容已保留，请核对后再提交。', { exact: true }).waitFor();
      assert.equal(await activity.inputValue(), '晚饭后散步五分钟');
      assert.equal(await panel.getByLabel('什么时候做', { exact: true }).inputValue(), '明天下午', 'untouched fields accept fresh server information');
      await panel.getByRole('button', { name: '收起', exact: true }).click();
      await page.evaluate(() => window.dispatchEvent(new Event('focus')));
      await page.waitForTimeout(100);
      assert.equal(await activity.count(), 0, 'same card does not reopen on polling');
      await panel.getByRole('button', { name: '继续填写', exact: true }).click();
      assert.equal(await activity.inputValue(), '晚饭后散步五分钟');
      await page.evaluate(() => { window.goalCardFixture.failPut = true; });
      await panel.getByRole('button', { name: '交给教练一起完善', exact: true }).click();
      await panel.getByRole('alert').waitFor();
      assert.equal(await activity.inputValue(), '晚饭后散步五分钟', 'failed/stale save preserves partial form');
      assert.equal((await page.evaluate(() => window.goalCardFixture.chats)).length, 0, 'rejected save cannot start chat');
      await page.evaluate(() => { window.goalCardFixture.failPut = false; });
      await panel.getByRole('button', { name: '交给教练一起完善', exact: true }).click();
      await panel.getByRole('button', { name: '确认这个安排', exact: true }).waitFor();
      const submission = await page.evaluate(() => window.goalCardFixture.chats.at(-1));
      assert.deepEqual(submission.metadata, { goal_card_id: 'primary-one', goal_card_revision: '3', goal_card_action: 'submit' });
      const write = await page.evaluate(() => window.goalCardFixture.writes.at(-1));
      assert.equal(write.fields.difficulty_rating, null); assert.equal(write.fields.duration_minutes, null);
      assert.equal(write.fields.schedule_text, '明天下午', 'partial local edit does not erase remotely added schedule');
      await page.evaluate(() => { window.goalCardFixture.failConfirm = true; });
      await panel.getByRole('button', { name: '确认这个安排', exact: true }).click();
      await waitFor(page, () => window.goalCardFixture.chats.at(-1).metadata.goal_card_action === 'confirm');
      assert.equal(await page.evaluate(() => window.goalCardFixture.card.phase), 'ready', 'failed confirmation never claims confirmed');
      await panel.getByRole('button', { name: '先查看或修改', exact: true }).click();
      assert.equal(await activity.inputValue(), '晚饭后散步五分钟', 'failed confirmation preserves server draft');
      await page.evaluate(() => { window.goalCardFixture.failConfirm = false; });
      await panel.getByRole('button', { name: '确认这个安排', exact: true }).click();
      await panel.getByText('目标信息已保存到“我的目标”。', { exact: true }).waitFor();
      assert.equal(await activity.count(), 0, 'server-confirmed card auto collapses');
      const confirm = await page.evaluate(() => window.goalCardFixture.chats.at(-1));
      assert.deepEqual(confirm.metadata, { goal_card_id: 'primary-one', goal_card_revision: '4', goal_card_action: 'confirm' });
      await page.evaluate(() => window.goalCardFixture.setCard('secondary', 'secondary-one'));
      await activity.waitFor();
      assert.equal(await panel.getByLabel('你觉得有多难', { exact: true }).count(), 0, 'secondary does not require core difficulty work');
      await activity.fill('周末去公园');
      await panel.getByRole('button', { name: '暂不继续细化，保留草稿', exact: true }).click();
      await panel.getByText('草稿已保留', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.goalCardFixture.card.fields.activity_content), '周末去公园', 'pausing retains locally typed partial fields on backend');
      await panel.getByRole('button', { name: '查看卡片', exact: true }).click();
      assert.equal(await activity.count(), 0, 'paused card is read-only until the coach reopens it');
      await panel.getByRole('button', { name: '继续完善', exact: true }).click();
      await activity.waitFor();
      assert.equal(await activity.inputValue(), '周末去公园');
      await panel.getByRole('button', { name: '交给教练一起完善', exact: true }).click();
      await panel.getByRole('button', { name: '确认这个安排', exact: true }).waitFor();
      await panel.getByRole('button', { name: '确认这个安排', exact: true }).click();
      await panel.getByText('目标信息已保存到“我的目标”。', { exact: true }).waitFor();
      await panel.getByRole('button', { name: '我的目标 ↗', exact: true }).click();
      const archive = page.getByRole('dialog', { name: '我的目标', exact: true });
      await archive.getByRole('region', { name: '已确认的次要目标', exact: true }).getByRole('heading', { name: '周末去公园', exact: true }).waitFor();
      assert.equal(await archive.getByText('已确认', { exact: true }).count(), 1, 'ordinary users can reach real secondary card archive');
      await noOverflow(page); await page.screenshot({ path: path.join(OUT, `secondary-archive-${width}.png`), fullPage: true, animations: 'disabled' });
      await archive.getByRole('button', { name: '关闭目标总览', exact: true }).click();
      await page.evaluate(() => { window.goalCardFixture.card = null; window.goalCardFixture.enabled = false; window.dispatchEvent(new Event('focus')); });
      await panel.getByRole('button', { name: '查看全部 ↗', exact: true }).waitFor();
      assert.equal(await panel.getByText(/暂不支持|先聊聊你的想法/).count(), 0, 'disabled feature keeps only the compact goals link');
      await page.evaluate(() => { window.goalCardFixture.enabled = true; window.goalCardFixture.setCard('primary', 'primary-unsent'); });
      await activity.waitFor(); await activity.fill('刷新之前尚未提交的活动');
      await panel.getByRole('button', { name: '收起', exact: true }).click();
      const counts = await page.evaluate(() => {
        sessionStorage.setItem('qa-goal-restore-card', JSON.stringify(window.goalCardFixture.card));
        return { writes: window.goalCardFixture.writes.length, chats: window.goalCardFixture.chats.length };
      });
      await page.reload();
      await panel.getByRole('button', { name: '继续填写', exact: true }).waitFor();
      assert.equal(await activity.count(), 0, 'refresh does not reopen a dismissed formulation stage');
      await panel.getByRole('button', { name: '继续填写', exact: true }).click();
      assert.equal(await activity.inputValue(), '刷新之前尚未提交的活动', 'same account and session recover unsent local draft');
      assert.deepEqual(await page.evaluate(() => window.goalCardFixture.unknown), []);
      assert.deepEqual(errors, []); assert.deepEqual(escaped, []);
      results.push({ width, passed: true, ...counts });
    } catch (error) {
      await page.screenshot({ path: path.join(OUT, `failure-${width}.png`), fullPage: true }).catch(() => {});
      await fs.writeFile(path.join(OUT, `failure-${width}.json`), JSON.stringify({ error: error.stack, errors, escaped, state: await page.evaluate(() => window.goalCardFixture).catch(() => null) }, null, 2));
      throw error;
    } finally { await context.close(); }
  }
  await fs.writeFile(path.join(OUT, 'results.json'), JSON.stringify(results, null, 2)); return results;
};

if (require.main === module) (async () => { const { chromium } = require('playwright'); const browser = await chromium.launch({ headless: true, channel: 'chrome' }); try { console.log(JSON.stringify(await exports.run(browser, process.argv[2]), null, 2)); } finally { await browser.close(); } })().catch(error => { console.error(error); process.exitCode = 1; });
