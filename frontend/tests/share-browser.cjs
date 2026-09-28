// Real-browser checks with synthetic snapshots; no accounts, database or model calls.
// Start Next dev, then SHARE_QA_URL=http://127.0.0.1:3108 node tests/share-browser.cjs
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const base = process.env.SHARE_QA_URL || 'http://127.0.0.1:3108';
const token = 'a'.repeat(43);
const references = {
  available: true, module: 'module_2', retrieval_outcome: 'returned',
  gate_reason: null, mediator_status: 'completed', mediator_reason: null,
  mediator_reasoning_content: 'SNAPSHOT_MEDIATOR_REASONING',
  mediator_guidance: 'SNAPSHOT_MEDIATOR_GUIDANCE', mediator_cautions: [],
  mediator_model: 'fixture-mediator', mediator_duration_ms: 75,
  context_withheld: false, validator_status: 'passed',
  recalled: [{ id: 'fixture-chunk', source: 'fixture-source', text: 'SNAPSHOT_KNOWLEDGE', score: 0.9, score_type: 'cosine' }],
  provided: [{ id: 'fixture-chunk', source: 'fixture-source', text: 'SNAPSHOT_KNOWLEDGE', score: 0.9, score_type: 'cosine' }],
};
const snapshot = {
  snapshot_version: 1, title: '分享验收对话', created_at: '2026-09-28T08:00:00Z',
  messages: [
    { id: 1, role: 'user', content: '我想开始散步。', knowledge_references: null },
    { id: 2, role: 'assistant', content: '**按已确认的计划开始。**\n<script>window.shareInjected=true</script>',
      reasoning_content: 'SNAPSHOT_REPLY_REASONING', routing_reasoning_content: 'SNAPSHOT_ROUTER_REASONING',
      model_name: 'fixture-reply', router_model_name: 'fixture-router',
      timing: { reply_thinking_ms: 100, reply_generation_ms: 250, router_processing_ms: 80 },
      knowledge_references: references },
  ],
};

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
  try {
    for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      const errors = [], requests = [];
      let revoked = false, unavailable = false;
      page.on('pageerror', error => errors.push(error.message));
      // Prove that even an unrelated saved login is not sent to the share API.
      await page.addInitScript(() => localStorage.setItem('psy-auth-token', 'UNRELATED_PRIVATE_LOGIN'));
      await page.route('**/api/**', async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        requests.push({ path, headers: request.headers(), method: request.method() });
        if (path !== `/api/shares/${token}`) return route.fulfill({ status: 403, json: { detail: 'Unexpected private API request' } });
        const data = unavailable ? { ...snapshot, messages: snapshot.messages.map(m => ({ ...m, knowledge_references: null })) } : snapshot;
        return route.fulfill({ status: revoked ? 404 : 200, json: revoked ? { detail: 'Share not found' } : data });
      });
      const response = await page.goto(`${base}/share/${token}`);
      assert.equal(response.status(), 200);
      assert.match(response.headers()['referrer-policy'], /no-referrer/);
      await page.getByText('分享验收对话', { exact: true }).waitFor();
      assert.equal(await page.locator('textarea').count(), 0);
      assert.equal(await page.getByRole('button', { name: '发送', exact: true }).count(), 0);
      await page.getByRole('button', { name: '调试详情', exact: true }).click();
      for (const [label, text] of [
        ['查看回复深度思考', 'SNAPSHOT_REPLY_REASONING'],
        ['查看路由深度思考', 'SNAPSHOT_ROUTER_REASONING'],
        ['对话参考 chunk', 'SNAPSHOT_KNOWLEDGE'],
        ['中介节点使用建议内容', 'SNAPSHOT_MEDIATOR_GUIDANCE'],
      ]) {
        await page.getByRole('button', { name: label, exact: true }).click();
        await page.getByText(text, { exact: true }).first().waitFor({ state: 'visible' });
      }
      // This is recorded data, never a new model invocation.
      const mediator = page.getByText('SNAPSHOT_MEDIATOR_REASONING', { exact: true });
      if (!(await mediator.isVisible())) {
        const details = page.locator('details').filter({ has: mediator });
        if (await details.count()) await details.locator('summary').click();
      }
      await mediator.waitFor({ state: 'visible' });
      assert.equal(await page.evaluate(() => window.shareInjected), undefined);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'no horizontal page overflow');
      assert.ok(requests.every(r => r.path === `/api/shares/${token}` && r.method === 'GET' && !r.headers.authorization && !r.headers.cookie));
      await page.screenshot({ path: `/tmp/ba-share-${viewport.width}.png`, fullPage: true });
      // Missing old reference data must not fall back to the owner's private route.
      unavailable = true;
      await page.reload();
      await page.getByRole('button', { name: '调试详情', exact: true }).click();
      await page.getByRole('button', { name: '对话参考 chunk', exact: true }).click();
      await page.getByText('本条回复未保存这项详情，分享中无法补填。', { exact: true }).waitFor();
      assert.ok(requests.every(r => r.path === `/api/shares/${token}`));
      revoked = true;
      await page.reload();
      await page.getByText('分享链接不存在或已撤销。', { exact: true }).waitFor();
      assert.equal(await page.getByText('SNAPSHOT_REPLY_REASONING', { exact: true }).count(), 0);
      assert.deepEqual(errors, []);
      await context.close();
    }
    const owner = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await owner.newPage();
    const ownerErrors = [];
    page.on('pageerror', error => ownerErrors.push(error.message));
    await page.addInitScript(() => {
      localStorage.setItem('psy-auth-token', 'fixture-owner');
      const original = window.fetch.bind(window);
      window.shareFixture = { shares: [], blocked: true, creates: 0, revokes: 0 };
      // Insecure/embedded-browser fallback: select the link when clipboard is unavailable.
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
      document.execCommand = () => false;
      window.fetch = async (input, options = {}) => {
        const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
        const fixture = window.shareFixture;
        const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
        const conversation = { session_id: 'share-fixture', title: '待分享对话', updated_at: '2026-09-28T08:00:00Z', pinned: false, revision: 1,
          messages: [{ id: 1, role: 'assistant', content: '欢迎，已有对话。' }], next_module: 'module_2' };
        if (path === '/api/auth/me') return json({ username: 'fixture-owner', nickname: '测试用户', role: 'user', profile_uuid: 'fixture-owner', email_required: false, birth_date_required: false });
        if (path === '/api/conversations') return json([conversation]);
        if (path === '/api/conversations/share-fixture') return json(conversation);
        if (path.endsWith('/revision')) return json({ revision: 1 });
        if (path.endsWith('/events')) return new Response(new ReadableStream({ start() {} }), { headers: { 'Content-Type': 'text/event-stream' } });
        if (path === '/api/conversations/share-fixture/shares') {
          if (options.method === 'POST') {
            fixture.creates++;
            if (fixture.blocked) return json({ detail: '这段对话仍在生成或保存，请完成后再创建分享。' }, 409);
            const share = { id: 'share-one', title: conversation.title, created_at: '2026-09-28T08:00:00Z', message_count: 1, snapshot_version: 1, revoked_at: null };
            fixture.shares.unshift(share);
            return json({ ...share, token: 'b'.repeat(43), path: '/share/' + 'b'.repeat(43) }, 201);
          }
          return json(fixture.shares);
        }
        if (path === '/api/conversations/share-fixture/shares/share-one' && options.method === 'DELETE') {
          fixture.revokes++;
          fixture.shares[0].revoked_at = new Date().toISOString();
          return new Response(null, { status: 204 });
        }
        if (path === '/api/assessment/history') return json({ items: [], has_more: false, next_offset: null });
        if (path.startsWith('/api/')) return json({});
        return original(input, options);
      };
    });
    await page.goto(base);
    await page.getByText('欢迎，已有对话。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '更多操作', exact: true }).click();
    await page.getByRole('menuitem', { name: '分享', exact: true }).click();
    const modal = page.getByRole('dialog', { name: '分享对话', exact: true });
    await modal.waitFor();
    await modal.getByRole('button', { name: '创建当前对话的分享链接', exact: true }).click();
    await modal.getByRole('alert').filter({ hasText: '仍在生成或保存' }).waitFor();
    await page.evaluate(() => { window.shareFixture.blocked = false; });
    await modal.getByRole('button', { name: '创建当前对话的分享链接', exact: true }).click();
    const input = modal.getByRole('textbox', { name: '分享链接', exact: true });
    await input.waitFor();
    assert.equal(await input.inputValue(), `${new URL(base).origin}/share/${'b'.repeat(43)}`);
    await modal.getByRole('button', { name: '复制链接', exact: true }).click();
    await modal.getByText('自动复制未成功，已选中链接，请手动复制。', { exact: true }).waitFor();
    await page.screenshot({ path: '/tmp/ba-share-modal.png', fullPage: true });
    await modal.getByRole('button', { name: '撤销 待分享对话 的分享', exact: true }).click();
    await modal.getByText('已撤销', { exact: true }).waitFor();
    assert.equal(await input.count(), 0);
    await modal.getByRole('button', { name: '关闭分享', exact: true }).click();
    await page.getByRole('button', { name: '更多操作', exact: true }).click();
    await page.getByRole('menuitem', { name: '分享', exact: true }).click();
    await modal.getByText('已撤销', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.shareFixture.creates), 2);
    assert.equal(await page.evaluate(() => window.shareFixture.revokes), 1);
    assert.deepEqual(ownerErrors, []);
    await owner.close();
    console.log('PASS: desktop/mobile shared UI, all four detail panels, stored mediator reasoning, no private/authenticated requests, missing-data isolation, safe text, revoked reload; owner create/reject/retry/copy fallback/revoke/reopen');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
