// Actual bundle, synthetic APIs only. Never sends prompts or writes production data.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { installFixtures } = require('./goal-formulation-1003.cjs');
const OUT = path.resolve(__dirname, '../../.test-tmp/conversation-selection');
function selectionFixture({ preset = null, failure = null, blockedStorage = false, member = false, initialHold = null, emptyList = false, listFailure = false } = {}) {
  const json = (v, status = 200) => new Response(JSON.stringify(v), { status, headers: { 'Content-Type': 'application/json' } });
  const key = id => `bacoach:selected-conversation:${id}`;
  const priorAccount = sessionStorage.getItem('qa-selection-account') || 'a';
  localStorage.setItem('psy-auth-token', priorAccount);
  if (preset && !sessionStorage.getItem('qa-selection-seeded')) {
    sessionStorage.setItem(key('a'), preset); sessionStorage.setItem('qa-selection-seeded', '1');
  }
  const f = window.selectionFixture = { calls: [], hold: initialHold, held: [], holdList: false, heldLists: [], missing: new Set(), failure, authHold: null, authHeld: [], expired: false };
  const account = () => localStorage.getItem('psy-auth-token');
  const detail = id => ({ session_id: id, title: id === 'old' ? '较早会话' : id === 'latest' ? '最新会话' : id === 'third' ? '另一会话' : 'B的会话', updated_at: id === 'old' ? '2026-10-01T00:00:00Z' : '2026-10-05T00:00:00Z', revision: 1, pinned: false, next_module: id === 'old' ? 'module_3' : 'module_2', routing_mode: 'router_only', reply_mode: 'ack_deep', reply_effort: 'low', messages: [{ id: id === 'old' ? 2 : 1, role: 'assistant', content: `正文-${id}` }] });
  const original = window.fetch.bind(window);
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href), p = url.pathname;
    if (!p.startsWith('/api/')) return original(input, options);
    const owner = account(); f.calls.push({ path: p, owner, method: options.method || 'GET' });
    if (p === '/api/auth/me') {
      if (f.authHold === owner) await new Promise(resolve => f.authHeld.push(resolve));
      if (f.expired) return json({}, 401);
      return json({ username: owner, profile_uuid: owner, role: member ? 'user' : 'admin', email_verified: true, email_required: false, nickname: owner });
    }
    if (p === '/api/conversations') {
      if (listFailure) return json({}, 503);
      const list = (emptyList ? [] : owner === 'b' ? ['b-only'] : ['latest', 'old', 'third']).filter(id => !f.missing.has(id)).map(detail);
      if (f.holdList) await new Promise(resolve => f.heldLists.push(resolve));
      return json(list);
    }
    if (p === '/api/conversations/current') return json(detail(owner === 'b' ? 'b-only' : 'latest'));
    const match = p.match(/^\/api\/conversations\/([^/]+)(.*)$/);
    if (match) {
      const id = decodeURIComponent(match[1]), tail = match[2];
      if (tail === '/events') return new Response(new ReadableStream({ start() {} }), { headers: { 'Content-Type': 'text/event-stream' } });
      if (tail === '/revision') return json({ revision: 1 });
      if (tail === '/context') return json({ estimated_tokens: 10, retained_messages: 1, total_messages: 1, mode: 'compression' });
      if (!tail) {
        const snapshot = detail(id);
        if (f.hold === id) await new Promise(resolve => f.held.push(resolve));
        if (f.missing.has(id)) return json({ detail: 'Unavailable' }, 404);
        if (id === 'old' && f.failure) return json({ detail: 'Synthetic failure' }, f.failure);
        if ((owner === 'b') !== (id === 'b-only')) return json({ detail: 'Unavailable' }, 404);
        return json(snapshot);
      }
    }
    const program = p.match(/^\/api\/program\/([^/]+)(.*)$/);
    if (program && program[1] !== 'goals') {
      const id = program[1];
      if (program[2] === '/goal-card') return json({ enabled: true, card: id === 'old' ? { id: 'old-card', kind: 'primary', phase: 'paused', revision: 1, fields: { activity_content: '较早会话的散步卡' }, concerns: [] } : null });
      return json({ enabled: true, runtime: { active_goal_id: null, row_version: 1 } });
    }
    return original(input, options);
  };
  if (blockedStorage) {
    const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
    Storage.prototype.getItem = function(k) { if (this === sessionStorage && k.startsWith('bacoach:')) throw new DOMException('blocked', 'SecurityError'); return get.call(this, k); };
    Storage.prototype.setItem = function(k, v) { if (this === sessionStorage && k.startsWith('bacoach:')) throw new DOMException('blocked', 'SecurityError'); return set.call(this, k, v); };
  }
}
async function selected(page, id) { await page.getByText(`正文-${id}`, { exact: true }).waitFor(); }
async function select(page, title, width) {
  if (width < 768) await page.getByRole('button', { name: '切换对话侧栏', exact: true }).click();
  await page.getByRole('navigation', { name: '对话历史', exact: true }).getByRole('button', { name: new RegExp(title) }).first().click();
}
async function stored(page, owner = 'a') { return page.evaluate(owner => sessionStorage.getItem(`bacoach:selected-conversation:${owner}`), owner); }
exports.run = async (browser, base, baseline = false) => {
  await fs.mkdir(OUT, { recursive: true });
  const rows = [];
  for (const width of [1440, 390]) {
    async function scenario(name, options, run) {
      const c = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
      const escaped = [], errors = [];
      await c.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== new URL(base).origin || url.pathname.startsWith('/api/')) { escaped.push(url.pathname); return route.fulfill({ status: 599, body: '{}' }); }
        return route.continue();
      });
      await c.addInitScript(installFixtures, { role: 'admin' });
      await c.addInitScript(selectionFixture, options);
      const p = await c.newPage(); p.setDefaultTimeout(12000); p.on('pageerror', e => errors.push(e.message));
      try {
        await p.goto(base); await run(p, c); assert.deepEqual(errors, []); assert.deepEqual(escaped, []);
        rows.push({ width, name, passed: true }); console.log(JSON.stringify(rows.at(-1)));
      } catch (e) { await p.screenshot({ path: path.join(OUT, `failure-${name}-${width}.png`) }).catch(() => {}); throw e; }
      finally { await c.close(); }
    }
    await scenario('refresh-card-daily', {}, async p => {
      await selected(p, 'latest'); const originalURL = p.url(); const historyLength = await p.evaluate(() => history.length);
      await select(p, '较早会话', width); await selected(p, 'old');
      await p.getByText('较早会话的散步卡', { exact: true }).waitFor();
      await p.reload();
      if (baseline) { await selected(p, 'latest'); await p.screenshot({ path: path.join(OUT, `baseline-${width}.png`) }); return; }
      await selected(p, 'old'); assert.equal(await stored(p), 'old');
      await p.getByText('较早会话的散步卡', { exact: true }).waitFor();
      await p.screenshot({ path: path.join(OUT, `restored-chat-${width}.png`) });
      assert.equal(p.url(), originalURL); assert.equal(await p.evaluate(() => history.length), historyLength);
      if (width < 768) await p.getByRole('button', { name: '切换对话侧栏', exact: true }).click();
      const nav = p.getByRole('navigation', { name: '对话历史', exact: true });
      assert.equal(await nav.getByRole('button', { name: /较早会话/ }).getAttribute('aria-current'), 'true');
      await nav.getByRole('button', { name: '每日记录', exact: true }).click(); await p.locator('dialog[open]').waitFor();
      await p.screenshot({ path: path.join(OUT, `restored-daily-${width}.png`) });
    });
    if (baseline) continue;
    await scenario('missing-saved-id', { preset: 'unknown-account-room' }, async p => {
      await selected(p, 'latest'); assert.equal(await stored(p), 'latest');
      assert.equal(await p.evaluate(() => window.selectionFixture.calls.some(c => c.path.includes('unknown-account-room'))), false);
    });
    for (const failure of [403, 404]) await scenario(`unavailable-${failure}`, { preset: 'old', failure }, async p => {
      await selected(p, 'latest'); assert.equal(await stored(p), 'latest');
      await p.reload(); await selected(p, 'latest');
    });
    await scenario('transient-does-not-forget', { preset: 'old', failure: 503 }, async p => {
      await p.getByText(/Loading the conversation failed \(503\)/).waitFor(); assert.equal(await stored(p), 'old');
      assert.equal(await p.getByText('正文-latest', { exact: true }).count(), 0);
      await p.evaluate(() => { window.selectionFixture.failure = null; });
      await select(p, '最新会话', width); await selected(p, 'latest'); await select(p, '较早会话', width); await selected(p, 'old');
    });
    await scenario('late-restore-cannot-replace-selection', {}, async p => {
      await selected(p, 'latest'); await p.evaluate(() => { window.selectionFixture.hold = 'old'; });
      await select(p, '较早会话', width); await p.waitForFunction(() => window.selectionFixture.held.length > 0);
      await select(p, '另一会话', width); await selected(p, 'third');
      await p.evaluate(() => { const f = window.selectionFixture; f.hold = null; f.held.splice(0).forEach(r => r()); });
      await p.waitForTimeout(200); await selected(p, 'third'); assert.equal(await stored(p), 'third');
    });
    await scenario('deleted-fallback-cannot-replace-selection', {}, async p => {
      await selected(p, 'latest');
      await p.evaluate(() => { const f = window.selectionFixture; f.failure = 404; f.holdList = true; });
      await select(p, '较早会话', width); await p.waitForFunction(() => window.selectionFixture.heldLists.length > 0);
      await select(p, '另一会话', width); await selected(p, 'third');
      await p.evaluate(() => { const f = window.selectionFixture; f.holdList = false; f.heldLists.splice(0).forEach(r => r()); });
      await p.waitForTimeout(200); await selected(p, 'third'); assert.equal(await stored(p), 'third');
    });
    await scenario('account-switch-late-read', {}, async (p, c) => {
      await selected(p, 'latest'); await p.evaluate(() => { window.selectionFixture.hold = 'old'; });
      await select(p, '较早会话', width); await p.waitForFunction(() => window.selectionFixture.held.length > 0);
      const other = await c.newPage(); await other.goto(base);
      await other.evaluate(() => localStorage.setItem('psy-auth-token', 'b'));
      await selected(p, 'b-only');
      await p.evaluate(() => { const f = window.selectionFixture; f.hold = null; f.held.splice(0).forEach(r => r()); });
      await p.waitForTimeout(200); await selected(p, 'b-only'); assert.equal(await stored(p, 'b'), 'b-only'); assert.equal(await stored(p, 'a'), 'latest');
      assert.equal(await p.getByText('正文-old', { exact: true }).count(), 0);
      await other.evaluate(() => localStorage.setItem('psy-auth-token', 'a')); await selected(p, 'latest');
    });
    await scenario('pending-initial-restore', { preset: 'old', initialHold: 'old' }, async p => {
      await p.waitForFunction(() => window.selectionFixture.held.length > 0);
      await select(p, '另一会话', width); await selected(p, 'third');
      await p.evaluate(() => { const f = window.selectionFixture; f.hold = null; f.held.splice(0).forEach(r => r()); });
      await p.waitForTimeout(200); await selected(p, 'third'); assert.equal(await stored(p), 'third');
    });
    await scenario('empty-history', { preset: 'old', emptyList: true }, async p => {
      await p.getByText('开始一段新对话', { exact: true }).waitFor(); assert.equal(await stored(p), null);
      assert.equal(await p.evaluate(() => window.selectionFixture.calls.some(c => c.method === 'POST')), false);
    });
    await scenario('list-failure-preserves-selection', { preset: 'old', listFailure: true }, async p => {
      await p.getByText('对话暂时加载失败，请刷新重试。已保存的对话会继续保留。', { exact: true }).waitFor(); assert.equal(await stored(p), 'old');
      assert.equal(await p.evaluate(() => window.selectionFixture.calls.some(c => c.path === '/api/conversations/old')), false);
    });
    await scenario('expired-auth-does-not-hang', {}, async p => {
      await selected(p, 'latest'); await p.evaluate(() => { window.selectionFixture.expired = true; window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })); });
      await p.locator('button[type="submit"]').filter({ hasText: '登录' }).waitFor(); assert.equal(await p.getByText('正文-latest', { exact: true }).count(), 0);
    });
    await scenario('blocked-storage', { blockedStorage: true }, async p => {
      await selected(p, 'latest'); await select(p, '较早会话', width); await selected(p, 'old'); await p.reload(); await selected(p, 'latest');
    });
    await scenario('member-keeps-server-current', { member: true, preset: 'old' }, async p => {
      await selected(p, 'latest'); assert.equal(await p.getByRole('navigation', { name: '对话历史' }).count(), 0);
      assert.equal(await p.evaluate(() => window.selectionFixture.calls.some(c => c.path === '/api/conversations/old')), false);
    });
    await scenario('back-forward-revalidation', {}, async p => {
      await selected(p, 'latest'); await select(p, '较早会话', width); await selected(p, 'old');
      await p.goto(base + '/_not-found'); await p.goBack(); await selected(p, 'old');
      await p.goForward(); await p.goBack(); await selected(p, 'old');
      await p.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
      await selected(p, 'old'); assert.equal(await stored(p), 'old');
    });
  }
  await fs.writeFile(path.join(OUT, baseline ? 'baseline.json' : 'results.json'), JSON.stringify(rows, null, 2)); return rows;
};
if (require.main === module) (async () => { const { chromium } = require('playwright'); const browser = await chromium.launch({ channel: 'chrome', headless: true }); try { await exports.run(browser, process.argv[2], process.argv.includes('--baseline')); } finally { await browser.close(); } })().catch(e => { console.error(e); process.exitCode = 1; });
