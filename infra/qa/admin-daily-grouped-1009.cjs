// Synthetic-only browser acceptance for the grouped administrator daily-record view.
// Run: NODE_PATH=<bundled node_modules> node infra/qa/admin-daily-grouped-1009.cjs
// BASE_URL defaults to the isolated dev server; no production API is contacted.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');
const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:3109';

async function installFixtures(context, theme) {
  await context.addInitScript(({ theme }) => {
    localStorage.setItem('psy-auth-token', 'synthetic');
    localStorage.setItem('psy-theme', theme);
    const original = window.fetch.bind(window);
    const fixture = window.fixture = { calls: [], exports: [], fail: '', slowUser: '', releaseSlow: null, submitted: false, edited: false };
    const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
    const users = Array.from({ length: 23 }, (_, i) => ({ subject_id: `subject-${i}`, username: `person${i}`, display_id: i === 0 ? '小河#12345' : i === 1 ? '小山#23456' : i === 22 ? '从未填写#99999' : `测试用户${i}#${10000+i}` }));
    const record = (id, localDate, activity, extra = {}) => ({ id, revision_no: 2, scale_version: 2, local_date: localDate, status: 'completed', timezone: 'Asia/Shanghai', completion_rate: null, completion_not_applicable: true, activity_level: 5, overall_mood: 0, social_connection: null, approach_vs_avoidance: null, reflection_note: fixture.edited && id === 1 ? '更新后的最终记录' : `当天感受-${id}`, activities: [
      { position: 0, time_slot: '07:30–08:00', activity, emotion: 0, achievement: null, connection: 3, enjoyment: null, importance: null, note: '保留零分与未填写的区别' },
      { position: 1, time_slot: '19:00–20:00', activity: `晚间活动-${id}`, emotion: 4, achievement: 2, connection: null, enjoyment: 3, importance: 5, note: '第二项活动完整保留' },
    ], ...extra });
    const allRecords = (subjectId) => subjectId === 'subject-22' ? [] : subjectId === 'subject-0' ? [
      ...Array.from({ length: 26 }, (_, i) => record(i + 1, new Date(Date.UTC(2026, 9, 9 - i)).toISOString().slice(0, 10), i ? `历史活动-${i}` : '早间散步')),
      // The production table currently enforces one record/day; this second record
      // is a forward-compatible UI boundary test, not a claim about live data.
      record(100, '2026-10-09', '同日第二份记录的活动', { scale_version: 1, completion_rate: 7, completion_not_applicable: false, overall_mood: 7 }),
    ] : [record(200 + Number(subjectId.split('-')[1]), '2026-10-08', `其他用户活动-${subjectId}`), ...(fixture.submitted && subjectId === 'subject-1' ? [record(500, today(), '刚提交的新记录')] : [])];
    const adminRows = (u) => allRecords(u.subject_id).map(record => ({ ...u, record }));
    const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    const conversation = { session_id: 'demo', title: '每日记录管理员验收', revision: 1, messages: [], updated_at: '2026-10-09T07:00:00Z', next_module: 'module_1' };
    window.fetch = async (input, options = {}) => {
      const url = new URL(typeof input === 'string' ? input : input.url, location.href), p = url.pathname;
      if (p === '/api/auth/me') return json({ username: 'synthetic', nickname: '管理员验收', role: localStorage.getItem('qa-user') ? 'user' : 'admin', profile_uuid: 'synthetic', email_required: false, email_verified: true });
      if (p === '/api/conversations') return json(options.method === 'POST' ? conversation : [conversation]);
      if (p === '/api/conversations/demo' || p === '/api/conversations/current') return json(conversation);
      if (p.endsWith('/revision')) return json({ revision: 1 });
      if (p.endsWith('/events')) return new Response(new ReadableStream({ start() {}, cancel() {} }), { headers: { 'Content-Type': 'text/event-stream' } });
      if (p === '/api/program/demo') return json({ enabled: false });
      if (p.startsWith('/api/admin/assessments')) {
        fixture.calls.push({ path: p, params: Object.fromEntries(url.searchParams) });
        if (fixture.fail && (fixture.fail === '*' || p.endsWith(fixture.fail))) return json({ detail: '模拟读取失败，请重试' }, 503);
        if (p === '/api/admin/assessments/users') {
          const query = url.searchParams.get('query') || '', offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 20);
          const matched = users.filter(u => [u.username, u.display_id, u.subject_id].some(x => x.includes(query))).map(u => {
            const records = allRecords(u.subject_id), dates = [...new Set(records.map(r => r.local_date))].sort().reverse();
            return { ...u, today_completed: records.some(r => r.local_date === today()), record_count: records.length, date_count: dates.length, last_record_date: dates[0] || null };
          });
          return json({ items: matched.slice(offset, offset + limit), total: matched.length, has_more: offset + limit < matched.length, today: today(), time_zone: 'Asia/Shanghai' });
        }
        const dateMatch = p.match(/^\/api\/admin\/assessments\/users\/([^/]+)\/dates$/);
        if (dateMatch) {
          const subjectId = decodeURIComponent(dateMatch[1]), records = allRecords(subjectId);
          if (fixture.slowUser === subjectId) await new Promise(resolve => { fixture.releaseSlow = resolve; }); // Deliberately ignore abort to exercise stale-response guards.
          const dates = [...new Set(records.map(r => r.local_date))].sort().reverse();
          return json({ subject_id: subjectId, items: dates.map(local_date => { const sameDay = records.filter(r => r.local_date === local_date); return { local_date, record_count: sameDay.length, activity_count: sameDay.reduce((sum, r) => sum + r.activities.length, 0) }; }), total_dates: dates.length });
        }
        const dayMatch = p.match(/^\/api\/admin\/assessments\/users\/([^/]+)\/days\/([^/]+)$/);
        if (dayMatch) {
          const subjectId = decodeURIComponent(dayMatch[1]), localDate = dayMatch[2], user = users.find(u => u.subject_id === subjectId);
          const items = adminRows(user).filter(r => r.record.local_date === localDate);
          return json({ subject_id: subjectId, local_date: localDate, items, total: items.length });
        }
        const revisionMatch = p.match(/^\/api\/admin\/assessments\/(\d+)\/revisions$/);
        if (revisionMatch) {
          const current = users.flatMap(adminRows).find(r => r.record.id === Number(revisionMatch[1])).record;
          return json([{ revision_no: 2, saved_at: '2026-10-09T06:00:00Z', record: current }, { revision_no: 1, saved_at: '2026-10-08T06:00:00Z', record: { ...current, revision_no: 1, reflection_note: '修改前的原始内容' } }]);
        }
        if (p.endsWith('/export.csv')) { fixture.exports.push(Object.fromEntries(url.searchParams)); return new Response('\ufeffrecord_id,scale_version\r\n1,2\r\n', { headers: { 'Content-Type': 'text/csv' } }); }
        if (p === '/api/admin/assessments') {
          const params = Object.fromEntries(url.searchParams), offset = Number(params.offset || 0);
          const matched = users.flatMap(adminRows).filter(r => (!params.query || r.username.includes(params.query) || r.display_id.includes(params.query)) && (!params.start || r.record.local_date >= params.start) && (!params.end || r.record.local_date <= params.end) && (!params.scale_version || r.record.scale_version === Number(params.scale_version)));
          return json({ items: matched.slice(offset, offset + 20), total: matched.length, users: new Set(matched.map(r => r.subject_id)).size, versions: { '1': 1, '2': matched.length - 1 }, has_more: offset + 20 < matched.length });
        }
      }
      if (p.startsWith('/api/')) return json({});
      return original(input, options);
    };
  }, { theme });
}

async function run(browser) {
  const out = path.resolve('.test-tmp/admin-daily-grouped-1009');
  await fs.mkdir(out, { recursive: true });
  const results = [];
  for (const [width, theme] of [[1440, 'warm'], [390, 'warm'], [1440, 'dark'], [390, 'dark']]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, isMobile: width < 600, hasTouch: width < 600, acceptDownloads: true, timezoneId: 'America/Los_Angeles' });
    await installFixtures(context, theme);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.stack || e.message));
    page.setDefaultTimeout(15000);
    await page.clock.install({ time: new Date('2026-10-09T12:00:00Z') });
    const shot = async name => { await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}.png`), scale: 'css' }); };
    try {
      await page.goto(BASE_URL);
      await page.getByRole('button', { name: /账号菜单/ }).click();
      await page.getByRole('menuitem', { name: '每日记录数据', exact: true }).click();
      const d = page.getByRole('dialog', { name: '每日记录数据', exact: true });
      const users = d.getByRole('region', { name: '用户每日记录列表', exact: true });
      const dates = d.getByRole('region', { name: '填写日期列表', exact: true });
      const detail = d.getByRole('region', { name: '当日全部记录', exact: true });
      const user = display => users.getByRole('button', { name: `查看 ${display} 的填写日期`, exact: true });
      await user('小河#12345').waitFor();
      assert.equal(await users.getByRole('button', { name: /^查看 .* 的填写日期$/ }).count(), 20);
      assert.match(await user('小河#12345').innerText(), /已填写/);
      assert.match(await user('小山#23456').innerText(), /今日未填写/);
      const unfilled = await user('小山#23456').getByText('今日未填写', { exact: true }).evaluate(el => ({ color: getComputedStyle(el).color, size: getComputedStyle(el).fontSize }));
      const filled = await user('小河#12345').getByText('已填写', { exact: true }).evaluate(el => ({ color: getComputedStyle(el).color, size: getComputedStyle(el).fontSize }));
      assert.notEqual(unfilled.color, filled.color); assert.ok(parseFloat(unfilled.size) <= 13);
      await shot('overview');
      await page.evaluate(() => { window.fixture.fail = '/dates'; });
      await user('小河#12345').click();
      await dates.getByRole('alert').filter({ hasText: '模拟读取失败' }).waitFor();
      await page.evaluate(() => { window.fixture.fail = ''; });
      await dates.getByRole('button', { name: '重试读取日期', exact: true }).click();
      await dates.getByRole('button', { name: '查看 2026-10-09 的全部记录', exact: true }).waitFor();
      assert.equal(await dates.getByRole('button', { name: /^查看 \d{4}-\d{2}-\d{2} 的全部记录$/ }).count(), 26);
      await page.evaluate(() => { window.fixture.fail = '/days/2026-10-09'; });
      await dates.getByRole('button', { name: '查看 2026-10-09 的全部记录', exact: true }).click();
      await detail.getByRole('alert').filter({ hasText: '模拟读取失败' }).waitFor();
      await page.evaluate(() => { window.fixture.fail = ''; });
      await detail.getByRole('button', { name: '重试读取当天记录', exact: true }).click();
      await detail.getByText('早间散步', { exact: true }).waitFor();
      await detail.getByText('同日第二份记录的活动', { exact: true }).waitFor();
      await detail.getByText('晚间活动-1', { exact: true }).waitFor();
      await detail.getByText('晚间活动-100', { exact: true }).waitFor();
      assert.equal(await detail.locator('[data-record-id]').count(), 2);
      assert.equal(await detail.getByRole('button', { name: '修改这份记录', exact: true }).count(), 0);
      await shot('detail');
      await detail.getByText('当天感受-1', { exact: true }).waitFor();
      assert.equal(await detail.getByRole('button', { name: /修改历史|历史版本/ }).count(), 0);
      assert.equal(await page.evaluate(() => window.fixture.calls.filter(c => c.path.endsWith('/revisions')).length), 0);
      await page.evaluate(() => { window.fixture.edited = true; });
      await d.getByRole('button', { name: '刷新用户填写状态', exact: true }).click();
      await page.waitForFunction(() => !document.querySelector('button[aria-label="刷新用户填写状态"]')?.disabled);
      assert.equal(await dates.getByRole('button', { name: '查看 2026-10-09 的全部记录', exact: true }).getAttribute('aria-pressed'), 'true');
      await detail.getByText('早间散步', { exact: true }).waitFor();
      await detail.getByText('同日第二份记录的活动', { exact: true }).waitFor();
      await detail.getByText('更新后的最终记录', { exact: true }).waitFor();
      if (width < 1024) {
        await d.getByRole('button', { name: '返回日期列表', exact: true }).click();
        assert.equal(await dates.getByRole('button', { name: '查看 2026-10-09 的全部记录', exact: true }).getAttribute('aria-pressed'), 'true');
        await d.getByRole('button', { name: '返回用户列表', exact: true }).click();
        assert.equal(await user('小河#12345').getAttribute('aria-pressed'), 'true');
      }
      await users.getByRole('button', { name: '下一页', exact: true }).click();
      await user('从未填写#99999').waitFor();
      assert.equal(await users.getByRole('button', { name: /^查看 .* 的填写日期$/ }).count(), 3);
      assert.match(await user('从未填写#99999').innerText(), /今日未填写/);
      await user('从未填写#99999').click();
      await dates.getByText(/尚未提交|还没有.*记录|暂无.*记录/).first().waitFor();
      assert.equal(await dates.getByRole('button', { name: /^查看 \d{4}/ }).count(), 0);
      await shot('empty');
      await d.getByRole('textbox', { name: '查找用户', exact: true }).fill('nobody');
      await d.getByRole('button', { name: '查找', exact: true }).click();
      await users.getByText('没有找到符合条件的普通用户。', { exact: true }).waitFor();
      await d.getByRole('textbox', { name: '查找用户', exact: true }).fill('从未填写');
      await d.getByRole('button', { name: '查找', exact: true }).click();
      await user('从未填写#99999').waitFor();
      assert.equal(await users.getByRole('button', { name: /^查看 .* 的填写日期$/ }).count(), 1);
      await d.getByRole('button', { name: '清除', exact: true }).click();
      await user('小河#12345').waitFor();
      await page.evaluate(() => { window.fixture.slowUser = 'subject-0'; });
      await user('小河#12345').click();
      await page.waitForFunction(() => typeof window.fixture.releaseSlow === 'function');
      await user('小山#23456').click();
      await dates.getByRole('button', { name: '查看 2026-10-08 的全部记录', exact: true }).waitFor();
      await page.evaluate(() => { window.fixture.slowUser = ''; window.fixture.releaseSlow(); window.fixture.releaseSlow = null; });
      await page.waitForTimeout(100);
      assert.equal(await dates.getByRole('button', { name: /^查看 \d{4}/ }).count(), 1);
      assert.equal(await dates.getByRole('button', { name: '查看 2026-10-09 的全部记录', exact: true }).count(), 0);
      await page.evaluate(() => { window.fixture.fail = '/users'; });
      await d.getByRole('button', { name: '刷新用户填写状态', exact: true }).click();
      await d.getByRole('alert').filter({ hasText: '模拟读取失败' }).waitFor();
      await page.evaluate(() => { window.fixture.fail = ''; });
      await d.getByRole('button', { name: /重试|重新加载/ }).first().click();
      await user('小山#23456').waitFor();
      // A newly completed record should update without closing the administrator view.
      await page.evaluate(() => { window.fixture.submitted = true; window.dispatchEvent(new Event('focus')); });
      await page.waitForFunction(() => [...document.querySelectorAll('button')].find(b => b.getAttribute('aria-label') === '查看 小山#23456 的填写日期')?.textContent.includes('已填写'));
      // Move across Beijing midnight. The browser deliberately uses a different TZ.
      await page.clock.setSystemTime(new Date('2026-10-09T15:59:30Z'));
      await page.clock.fastForward(65000);
      await page.waitForFunction(() => [...document.querySelectorAll('button')].find(b => b.getAttribute('aria-label') === '查看 小河#12345 的填写日期')?.textContent.includes('今日未填写'));
      await shot('midnight');
      // The existing read-only export path must remain available.
      await d.getByText('筛选与导出', { exact: true }).click();
      await d.getByRole('textbox', { name: '导出用户筛选', exact: true }).fill('person0');
      assert.equal(await d.getByRole('button', { name: '导出每日汇总', exact: true }).isDisabled(), true);
      await d.getByRole('button', { name: '查询', exact: true }).click();
      for (const [label, kind] of [['导出每日汇总', 'daily'], ['导出活动明细', 'activities']]) {
        const downloaded = page.waitForEvent('download');
        await d.getByRole('button', { name: label, exact: true }).click();
        assert.equal((await downloaded).suggestedFilename(), `daily-records-${kind}.csv`);
        assert.equal(await page.evaluate(() => window.fixture.exports.at(-1).query), 'person0');
      }
      assert.equal(await page.evaluate(() => window.fixture.exports.length), 2);
      await d.getByRole('button', { name: '重置', exact: true }).click();
      await d.getByLabel('开始日期', { exact: true }).fill('2026-10-10');
      await d.getByLabel('结束日期', { exact: true }).fill('2026-10-09');
      await d.getByRole('button', { name: '查询', exact: true }).click();
      await d.getByRole('alert').filter({ hasText: '开始日期不能晚于结束日期' }).waitFor();
      await d.getByRole('button', { name: '重置', exact: true }).click();
      const fit = await d.evaluate(el => ({ width: el.clientWidth, scroll: el.scrollWidth, viewport: innerWidth, doc: document.documentElement.scrollWidth, overflowing: [...el.querySelectorAll('section, article')].filter(x => x.scrollWidth > x.clientWidth + 1).map(x => x.getAttribute('aria-label') || x.tagName) }));
      assert.equal(fit.viewport, fit.doc); assert.ok(fit.scroll <= fit.width + 1); assert.deepEqual(fit.overflowing, []);
      assert.deepEqual(errors, []);
      await d.getByRole('button', { name: '关闭每日记录数据', exact: true }).click();
      await page.evaluate(() => localStorage.setItem('qa-user', 'true'));
      await page.reload(); await page.getByRole('button', { name: /账号菜单/ }).click();
      assert.equal(await page.getByRole('menuitem', { name: '每日记录数据', exact: true }).count(), 0);
      const result = { width, theme, passed: true, userPagination: true, allDates: 26, sameDayRecords: 2, activities: 4, finalVersionOnly: true, staleResponse: true, retry: true, autoRefresh: true, beijingMidnight: true, exports: true, adminOnly: true, fit, colors: { unfilled, filled }, errors };
      results.push(result); console.log(JSON.stringify(result));
    } catch (error) {
      await shot('FAIL').catch(() => {});
      await fs.writeFile(path.join(out, `FAIL-${width}-${theme}.txt`), await page.locator('body').innerText().catch(() => 'page unavailable'));
      throw error;
    } finally { await context.close(); }
  }
  await fs.writeFile(path.join(out, 'results.json'), JSON.stringify(results, null, 2));
  return results;
}
module.exports = { run };
if (require.main === module) (async () => { const browser = await chromium.launch({ headless: true, channel: 'chrome' }); try { await run(browser); } finally { await browser.close(); } })().catch(error => { console.error(error); process.exitCode = 1; });
