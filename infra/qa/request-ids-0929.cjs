/* Local, synthetic UI acceptance; never calls a live model or database. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');
const base = process.env.REQUEST_IDS_QA_URL || 'http://127.0.0.1:3107';
const requests = [
  { stage: 'main_generation', request_id: 'main-79cb-example', provider: 'deepseek', model: 'qwen-test',
    recorded_at: '2026-09-29T03:00:03Z', duration_ms: 850, error_code: null },
  { stage: 'module_router', request_id: 'router-97bc-example', provider: 'deepseek', model: 'qwen-test' },
  { stage: 'knowledge_mediator', request_id: 'mediator-43ca-example', provider: 'deepseek', model: 'qwen-test' },
  { stage: 'main_generation_recovery', request_id: null, provider: 'deepseek', model: 'qwen-test' },
];
const messages = [{ id: 101, role: 'user', content: '今天可以先走十分钟。', created_at: '2026-09-29T03:00:00Z' },
  { id: 102, role: 'assistant', reply_to_message_id: 101, content: '好，我们就按你刚刚说的十分钟来。', created_at: '2026-09-29T03:00:03Z' }];
const detail = { session_id: 'request-demo', title: '请求 ID 本地验收', next_module: 'module_2',
  revision: 2, messages, updated_at: '2026-09-29T03:00:00Z' };

(async () => {
  const out = path.resolve(process.env.REQUEST_IDS_QA_OUT || '.test-tmp/request-ids-0929');
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    for (const width of [1440, 390]) {
      for (const mode of ['admin', 'user', 'share', 'old-share']) {
        const context = await browser.newContext({ viewport: { width, height: 1000 },
          permissions: ['clipboard-read', 'clipboard-write'] });
        await context.addInitScript(() => {
          localStorage.setItem('psy-auth-token', 'synthetic-only');
          const realFetch = window.fetch.bind(window);
          window.fetch = (input, options) => {
            const url = typeof input === 'string' ? input : input.url;
            if (new URL(url, location.href).pathname.endsWith('/events')) {
              return Promise.resolve(new Response(new ReadableStream({ start() {}, cancel() {} }),
                { headers: { 'Content-Type': 'text/event-stream' } }));
            }
            return realFetch(input, options);
          };
        });
        let privateRequests = 0;
        let privateApiRequests = 0;
        let failNext = false;
        await context.route('**/api/**', async route => {
          const p = new URL(route.request().url()).pathname;
          if (!p.startsWith('/api/shares/')) privateApiRequests++;
          let body = {};
          let status = 200;
          if (p === '/api/auth/me') body = { username: 'synthetic', nickname: '本地验收',
            role: mode === 'user' ? 'user' : 'admin', profile_uuid: 'synthetic',
            email_required: false, email_verified: true, birth_date_required: false };
          else if (p === '/api/conversations') body = [detail];
          else if (p === '/api/conversations/request-demo' || p === '/api/conversations/current') body = detail;
          else if (p.endsWith('/revision')) body = { revision: 2 };
          else if (p.endsWith('/requests')) {
            privateRequests++;
            assert.equal(p, '/api/conversations/messages/102/requests');
            assert.equal(route.request().headers().authorization, 'Bearer synthetic-only');
            if (failNext) { status = 503; failNext = false; }
            body = { user_sent_at: messages[0].created_at, assistant_created_at: messages[1].created_at, requests };
          } else if (p.startsWith('/api/shares/')) {
            assert.equal(route.request().headers().authorization, undefined);
            assert.equal(route.request().headers().cookie, undefined);
            body = { snapshot_version: 1,
              title: '分享请求 ID 验收', created_at: '2026-09-29T03:00:00Z',
              messages: messages.map(m => ({ id: m.id, role: m.role, content: m.content, created_at: m.created_at,
                // Legacy rich snapshots must stay transcript-only even if a stale
                // response contains private fields. No private API may fill them in.
                ...(mode === 'old-share' && m.role === 'assistant' ? {
                  model_requests: requests, request_records: { requests },
                  reasoning_content: 'LEGACY_PRIVATE_THOUGHT',
                  knowledge_references: { mediator_guidance: 'LEGACY_PRIVATE_GUIDANCE' },
                } : {}) })) };
          }
          else if (p.startsWith('/api/program/')) body = { enabled: false };
          await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        await page.goto(mode.includes('share') ? `${base}/share/${'a'.repeat(43)}` : base);
        await page.getByText(messages[1].content, { exact: true }).waitFor();
        if (mode !== 'admin') {
          assert.equal(await page.getByRole('button', { name: '调试详情', exact: true }).count(), 0);
          assert.equal(await page.getByRole('button', { name: '请求记录', exact: true }).count(), 0);
          if (mode.includes('share')) {
            assert.equal(privateApiRequests, 0);
            assert.doesNotMatch(await page.locator('body').innerText(), /LEGACY_PRIVATE|main-79cb-example|router-97bc-example|mediator-43ca-example/);
          }
        } else {
          await page.getByRole('button', { name: '调试详情', exact: true }).click();
          assert.equal(await page.getByRole('button', { name: '请求记录', exact: true }).count(), 1);
          await page.getByRole('button', { name: '请求记录', exact: true }).click();
          const panel = page.getByRole('region', { name: '请求记录', exact: true });
          assert.equal(await panel.count(), 1);
          await panel.getByText(requests[0].request_id, { exact: true }).waitFor();
          await panel.getByRole('button', { name: '复制主回复请求 ID', exact: true }).click();
          assert.equal(await page.evaluate(() => navigator.clipboard.readText()), requests[0].request_id);
          const content = await panel.innerText();
          assert.match(content, /用户发送：2026\/9\/29 11:00:00/);
          assert.match(content, /助手回复：2026\/9\/29 11:00:03/);
          assert.match(content, /记录时间：2026\/9\/29 11:00:03/);
          assert.match(content, /调用耗时：0\.85 秒/);
          assert.match(content, /主回复重试 Request ID：未记录/);
          await panel.getByText('其他节点的请求记录（2）', { exact: true }).click();
          await panel.getByText(requests[1].request_id, { exact: true }).waitFor();
          await panel.getByText(requests[2].request_id, { exact: true }).waitFor();
          failNext = true;
          await panel.getByRole('button', { name: '刷新请求记录', exact: true }).click();
          await panel.getByRole('alert').waitFor();
          await panel.getByRole('button', { name: '重试', exact: true }).click();
          await panel.getByText(requests[0].request_id, { exact: true }).waitFor();
          assert(privateRequests >= 3);
        }
        await page.screenshot({ path: path.join(out, `${mode}-${width}.png`) });
        if (mode !== 'admin') assert.equal(privateRequests, 0);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
        assert.deepEqual(errors, []);
        await context.close();
      }
    }
    console.log('8 UI scenarios passed: desktop/mobile admin, user, new transcript share, old rich snapshot; one request panel, API timestamps, copying, retry, public isolation.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
