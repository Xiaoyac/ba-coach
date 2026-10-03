// Synthetic browser regression: every API call is mocked, with a network-level
// /api/ backstop. This script never creates real accounts or writes backend data.
// Usage: NODE_PATH=<playwright modules> node infra/qa/daily-experience-1002.cjs [baseUrl]
// A caller may instead supply its browser: await require(file).run(browser, baseUrl).
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const SUMMARY = ['想做的事情完成程度', '这一天总体身体活动程度', '这一天整体心情如何？'];
const OUT = path.resolve(__dirname, '../../.test-tmp/daily-experience-1002/browser');

function installFixtures({ role = 'user' } = {}) {
  localStorage.setItem('psy-auth-token', 'synthetic-daily-experience-1002');
  localStorage.setItem('psy-theme', 'warm');
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const record = (id, date, values = {}) => ({
    id, local_date: date, timezone: 'Asia/Shanghai', revision_no: 1, scale_version: 2,
    status: 'completed', completion_not_applicable: false, completion_rate: 3,
    activity_level: 2, overall_mood: 3, social_connection: null,
    approach_vs_avoidance: null, reflection_note: null, activities: [], ...values,
  });
  const f = window.dailyExperienceFixture = {
    today, module: 'module_3', revision: 1, calls: [], writes: [], unknown: [],
    failSave: false, failDate: false, holdDate: false, pendingDates: [], nextId: 100,
    records: [
      record(7, '2020-01-07', { completion_rate: 5, activity_level: 4, overall_mood: 5 }),
      record(6, '2020-01-06', { completion_rate: 2, activity_level: 3, overall_mood: null }),
      record(3, '2020-01-03', { completion_rate: null, completion_not_applicable: true, activity_level: 1, overall_mood: 2 }),
      record(2, '2020-01-02', {
        revision_no: 7, completion_rate: 0, activity_level: 0, overall_mood: 0,
        reflection_note: '原来的整天感受', activities: [{
          position: 0, time_slot: '19:00–20:00', activity: '晚饭后散步十分钟', emotion: 0,
          achievement: 0, connection: 2, enjoyment: null, importance: 5, note: '保留的活动备注',
        }],
      }),
      record(1, '2020-01-01', {
        scale_version: 1, completion_rate: 9, activity_level: 8, overall_mood: 10,
        social_connection: 7, approach_vs_avoidance: 6, reflection_note: '旧版量表记录',
      }),
    ],
  };
  const json = (value, status = 200) => new Response(JSON.stringify(value), {
    status, headers: { 'Content-Type': 'application/json' },
  });
  const detail = () => ({
    session_id: 'daily-experience', title: '每日记录浏览器验收', revision: f.revision,
    updated_at: '2026-10-02T00:00:00Z', next_module: f.module, pinned: false,
    messages: [{ id: 10, role: 'assistant', content: `模拟对话 ${f.module}：可以按自己的节奏继续。` }],
  });
  const controllers = new Set();
  f.setModule = module => {
    f.module = module;
    f.revision++;
    const bytes = new TextEncoder().encode(`event: snapshot\ndata: ${JSON.stringify(detail())}\n\n`);
    for (const controller of controllers) controller.enqueue(bytes);
  };
  f.releaseDates = () => {
    f.holdDate = false;
    for (const resolve of f.pendingDates.splice(0)) resolve();
  };
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const p = url.pathname;
    if (!p.startsWith('/api/')) return originalFetch(input, options);
    const method = options.method || (typeof input === 'object' && input.method) || 'GET';
    f.calls.push({ path: p, method, search: url.search });
    if (p === '/api/auth/me') return json({
      username: 'synthetic', nickname: '本地普通用户', role, profile_uuid: 'synthetic',
      email_required: false, email_verified: true, birth_date_required: false,
    });
    if (p === '/api/modules') return json({ modules: ['module_1', 'module_2', 'module_3', 'module_4'] });
    if (p === '/api/message-feedback' && method === 'GET') return json([]);
    if (p === '/api/conversations/daily-experience/context' && method === 'GET') return json({
      estimated_tokens: 20, token_budget: null, retained_messages: 1, summarized_messages: 0,
      total_messages: 1, message_budget: null, mode: 'compression',
    });
    if (p === '/api/conversations') return json(method === 'POST' ? detail() : [detail()]);
    if (p === '/api/conversations/current' || p === '/api/conversations/daily-experience') return json(detail());
    if (p.endsWith('/revision')) return json({ revision: f.revision });
    if (p.endsWith('/events')) {
      let streamController;
      return new Response(new ReadableStream({
        start(controller) { streamController = controller; controllers.add(controller); },
        cancel() { controllers.delete(streamController); },
      }), { headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (p === '/api/assessment/history') return json({
      items: [...f.records].sort((a, b) => b.local_date.localeCompare(a.local_date)), has_more: false, next_offset: null,
    });
    if (p === '/api/assessment/by-date') {
      const value = structuredClone(f.records.find(r => r.local_date === url.searchParams.get('local_date')) || null);
      if (f.holdDate) await new Promise(resolve => f.pendingDates.push(resolve));
      return f.failDate ? json({ detail: '模拟今日状态读取失败' }, 503) : json(value);
    }
    if (p === '/api/assessment' || /^\/api\/assessment\/\d+$/.test(p)) {
      const body = JSON.parse(options.body);
      f.writes.push({ path: p, method, body: structuredClone(body) });
      if (f.failSave) return json({ detail: '模拟保存失败，请保留填写内容后重试。' }, 503);
      const previous = method === 'PUT' ? f.records.find(r => r.id === Number(p.split('/').pop())) : null;
      if (previous && body.expected_revision !== previous.revision_no) return json({ detail: '修改版本不匹配' }, 409);
      const saved = record(previous?.id || f.nextId++, body.local_date, {
        ...previous, ...body.summary, local_date: body.local_date,
        revision_no: previous ? previous.revision_no + 1 : 1,
        activities: body.activities.map((activity, position) => ({ ...activity, position })),
      });
      f.records = [...f.records.filter(r => r.id !== saved.id), saved];
      f.lastSaved = structuredClone(saved);
      return json(saved, method === 'POST' ? 201 : 200);
    }
    // These read-only panels are not opened in this suite, but normal workspace
    // startup can request their availability without affecting the daily flow.
    if (p.startsWith('/api/program/') && method === 'GET') return json({ enabled: false, goals: [], items: [] });
    if (p.startsWith('/api/push/') && method === 'GET') return json({ enabled: false, available: false, subscribed: false });
    f.unknown.push({ path: p, method });
    return json({ detail: `Unmocked API: ${method} ${p}` }, 501);
  };
}

async function enabled(locator, expected = true) {
  await locator.waitFor({ state: 'visible' });
  await locator.evaluate((element, expectedEnabled) => new Promise((resolve, reject) => {
    const start = performance.now();
    const check = () => {
      if (!element.disabled === expectedEnabled) return resolve();
      if (performance.now() - start > 5000) return reject(new Error(`Expected enabled=${expectedEnabled}`));
      requestAnimationFrame(check);
    };
    check();
  }), expected);
}

async function assertNoOverflow(page, label) {
  const problems = await page.evaluate(() => {
    const issues = [];
    if (document.documentElement.scrollWidth > innerWidth + 1) issues.push('document horizontal overflow');
    for (const element of document.querySelectorAll('dialog[open], [data-daily-scroll], [aria-label="每日记录快捷入口"], [popover]:popover-open')) {
      const box = element.getBoundingClientRect();
      if (!box.width || !box.height) continue;
      if (box.left < -1 || box.right > innerWidth + 1) issues.push(`${element.tagName} outside viewport`);
      if (element.scrollWidth > element.clientWidth + 1) issues.push(`${element.tagName} horizontal overflow`);
    }
    return issues;
  });
  assert.deepEqual(problems, [], label);
}

async function openRecord(page) {
  await page.getByRole('region', { name: '每日记录快捷入口' }).getByRole('button', { name: /^(记录今天|修改今天)$/ }).click();
  await page.getByRole('dialog', { name: /^(每日行为记录|修改行为记录)$/ }).waitFor();
  return page.locator('dialog[open]');
}

async function openHistory(page) {
  await page.getByRole('region', { name: '每日记录快捷入口' }).getByRole('button', { name: '历史与趋势', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '趋势与历史记录', exact: true });
  await dialog.getByRole('heading', { name: '近期变化', exact: true }).waitFor();
  return dialog;
}

async function closeDialog(page) {
  await page.locator('dialog[open]').getByRole('button', { name: '关闭', exact: true }).click();
  await page.locator('dialog[open]').waitFor({ state: 'hidden' });
}

async function setScores(dialog, values) {
  for (let index = 0; index < SUMMARY.length; index++) {
    await dialog.getByRole('radio', { name: `${SUMMARY[index]} ${values[index]}`, exact: true }).click();
  }
}

async function verifyTrends(page, width) {
  const dialog = await openHistory(page);
  const trends = dialog.getByRole('region', { name: '近期变化', exact: true });
  await trends.getByText('旧版总体评分为 0–10 分，不并入此图，仍可在历史记录中查看。', { exact: true }).waitFor();
  await trends.getByRole('button', { name: '身体活动', exact: true }).click();
  const plot = trends.getByRole('img');
  const points = await plot.locator('circle').evaluateAll(nodes => nodes.map(node => ({
    x: Number(node.getAttribute('cx')), title: node.textContent,
  })));
  assert.equal(points.length, 4, 'only modern records belong to the trend');
  assert.ok(points.every(point => !point.title.includes('2020-01-01')), 'legacy 0–10 values are excluded');
  assert.ok(points[0].title.includes('2020-01-02') && points[0].title.includes('0/5'), 'an explicit zero is a real plotted value');
  const gap1 = points[1].x - points[0].x;
  const gap3 = points[2].x - points[1].x;
  assert.ok(Math.abs(gap3 / gap1 - 3) < 0.001, 'January 3 to 6 uses three calendar days of horizontal spacing');
  assert.deepEqual((await plot.locator('path').getAttribute('d')).match(/[ML]/g), ['M', 'L', 'M', 'L'], 'line breaks across unrecorded calendar days');
  await trends.getByRole('button', { name: '完成程度', exact: true }).click();
  assert.equal(await trends.locator('circle').count(), 3, 'N/A must leave a gap, not become zero');
  assert.equal((await trends.locator('circle title').allTextContents()).some(text => text.includes('2020-01-03')), false);
  await trends.locator('summary').filter({ hasText: '查看各日期分数' }).click();
  const table = trends.getByRole('table');
  assert.equal(await table.locator('tbody tr').count(), 4);
  assert.equal(await table.getByRole('row').filter({ hasText: '2020-01-03' }).getByRole('cell').last().innerText(), '不适用');
  assert.equal(await table.getByRole('row').filter({ hasText: '2020-01-06' }).getByRole('cell').first().innerText(), '未填写');
  await trends.getByRole('button', { name: '整体心情', exact: true }).click();
  assert.equal(await trends.locator('circle').count(), 3, 'missing mood must not become zero');
  assert.deepEqual((await trends.locator('path').getAttribute('d')).match(/[ML]/g), ['M', 'L', 'M']);
  await assertNoOverflow(page, `history ${width}`);
  await page.screenshot({ path: path.join(OUT, `trends-${width}.png`), fullPage: true });
  await closeDialog(page);
}

async function verifyHelp(page, dialog, mobile) {
  const trigger = dialog.getByRole('button', { name: `解释：${SUMMARY[1]}`, exact: true });
  if (mobile) await trigger.tap(); else await trigger.click();
  const popup = page.getByRole('dialog', { name: SUMMARY[1], exact: true });
  await popup.waitFor();
  await popup.getByText(/没有运动，也可以如实记录这一天/).waitFor();
  await assertNoOverflow(page, 'term explanation');
  await page.screenshot({ path: path.join(OUT, `term-help-${page.viewportSize().width}.png`), fullPage: true, animations: 'disabled' });
  await page.keyboard.press('Escape');
  await popup.waitFor({ state: 'hidden' });
  assert.equal(await dialog.isVisible(), true, 'Escape closes the explanation without discarding the record form');
  await trigger.focus();
  await page.keyboard.press('Enter');
  await popup.waitFor();
  await popup.getByRole('button', { name: '关闭解释', exact: true }).click();
  await popup.waitFor({ state: 'hidden' });
  assert.equal(await dialog.isVisible(), true);
}

async function verifySummaryOnly(page, width) {
  const dialog = await openRecord(page);
  const save = dialog.getByRole('button', { name: '保存记录', exact: true });
  await enabled(save, false);
  assert.equal(await dialog.locator('[role="radio"][aria-checked="true"]').count(), 0, 'no score may be preselected, including hidden optional scores');
  assert.equal(await dialog.getByRole('radiogroup').count(), 3, 'the initial form shows only the three overall questions');
  const activities = dialog.locator('summary').filter({ hasText: /^补充活动明细/ }).locator('..');
  assert.equal(await activities.getAttribute('open'), null, 'activity details start collapsed');
  const summaryBeforeActivities = await dialog.evaluate(node => {
    const summary = node.querySelector('[data-daily-summary-panel]');
    const optional = node.querySelector('details');
    return !!(summary.compareDocumentPosition(optional) & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  assert.equal(summaryBeforeActivities, true, 'summary questions precede optional activity cards');
  await page.screenshot({ path: path.join(OUT, `form-empty-${width}.png`), fullPage: true, animations: 'disabled' });
  await verifyHelp(page, dialog, width < 600);
  await setScores(dialog, [0, 0, 0]);
  await dialog.getByRole('textbox', { name: '想再写一点（可留空）', exact: true }).fill('今天没有运动，只想记录整天感受。');
  await enabled(save);
  await activities.locator(':scope > summary').click();
  const optional = dialog.locator('summary').filter({ hasText: /^其他感受（4 项选填）/ });
  assert.equal(await optional.locator('..').getAttribute('open'), null, 'four optional feelings start collapsed');
  await dialog.getByRole('textbox', { name: '活动 1 的内容', exact: true }).fill('只写了活动名');
  await activities.locator(':scope > summary').click();
  await enabled(save, false);
  const incomplete = dialog.getByRole('button', { name: '请补全已填写活动的时间、内容和心情，或删除该活动', exact: true });
  await incomplete.click();
  await dialog.getByRole('textbox', { name: '活动 1 的内容', exact: true }).waitFor();
  assert.equal(await dialog.getByRole('textbox', { name: '活动 1 的内容', exact: true }).inputValue(), '只写了活动名');
  await dialog.getByRole('button', { name: '删除活动 1', exact: true }).click();
  await enabled(save);
  await assertNoOverflow(page, `form ${width}`);
  await page.screenshot({ path: path.join(OUT, `summary-only-${width}.png`), fullPage: true });
  await page.evaluate(() => { window.dailyExperienceFixture.failSave = true; });
  await save.click();
  await dialog.getByRole('alert').filter({ hasText: '模拟保存失败' }).waitFor();
  assert.equal(await dialog.getByRole('textbox', { name: '想再写一点（可留空）', exact: true }).inputValue(), '今天没有运动，只想记录整天感受。');
  for (const label of SUMMARY) assert.equal(await dialog.getByRole('radio', { name: `${label} 0`, exact: true }).getAttribute('aria-checked'), 'true');
  await page.evaluate(() => { window.dailyExperienceFixture.failSave = false; });
  await save.click();
  const saved = page.getByRole('dialog', { name: '记录已保存', exact: true });
  await saved.waitFor();
  await saved.getByText(/已保存整天的感受，没有填写活动明细/).waitFor();
  const state = await page.evaluate(() => ({ writes: window.dailyExperienceFixture.writes, lastSaved: window.dailyExperienceFixture.lastSaved }));
  assert.equal(state.writes.length, 2);
  assert.deepEqual(state.writes[0], state.writes[1], 'retry submits exactly the retained draft');
  assert.equal(state.writes[1].method, 'POST');
  assert.equal(state.writes[1].body.activities.length, 0);
  assert.deepEqual(SUMMARY.map((_, index) => state.writes[1].body.summary[['completion_rate', 'activity_level', 'overall_mood'][index]]), [0, 0, 0]);
  assert.equal(state.writes[1].body.summary.completion_not_applicable, false);
  assert.equal(state.lastSaved.id, 100, 'the fixture returns a full persisted record, including ID and revision');
  assert.equal(state.lastSaved.revision_no, 1);
  await saved.getByText(`${state.lastSaved.local_date} 的记录已保存`, { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector('[aria-label="每日记录快捷入口"] [role="status"]')?.textContent === '今天已记录');
  await page.screenshot({ path: path.join(OUT, `saved-${width}.png`), fullPage: true });
  await saved.getByRole('button', { name: '查看我的趋势与历史', exact: true }).click();
  await page.getByRole('dialog', { name: '趋势与历史记录', exact: true }).getByRole('heading', { name: '近期变化', exact: true }).waitFor();
  await page.getByText('今天没有运动，只想记录整天感受。', { exact: true }).waitFor();
  await closeDialog(page);
  const entry = page.getByRole('region', { name: '每日记录快捷入口' });
  await entry.getByText('今天已记录', { exact: true }).waitFor();
  await entry.getByRole('button', { name: '修改今天', exact: true }).waitFor();
  const edited = await openRecord(page);
  await page.getByRole('dialog', { name: '修改行为记录', exact: true }).waitFor();
  assert.equal(await edited.getByRole('textbox', { name: '想再写一点（可留空）', exact: true }).inputValue(), '今天没有运动，只想记录整天感受。');
  for (const label of SUMMARY) assert.equal(await edited.getByRole('radio', { name: `${label} 0`, exact: true }).getAttribute('aria-checked'), 'true');
  await closeDialog(page);
}

async function verifyDelayedReadProtectsKeyboardDraft(page) {
  for (const scenario of ['score', 'time']) {
    await page.evaluate(() => { window.dailyExperienceFixture.holdDate = true; });
    const dialog = await openRecord(page);
    await page.waitForFunction(() => window.dailyExperienceFixture.pendingDates.length > 0);
    if (scenario === 'score') {
      await dialog.getByRole('radio', { name: `${SUMMARY[0]} 0`, exact: true }).focus();
      // End invokes the radiogroup keyboard callback, with no click/input event.
      await page.keyboard.press('End');
      assert.equal(await dialog.getByRole('radio', { name: `${SUMMARY[0]} 5`, exact: true }).getAttribute('aria-checked'), 'true');
    } else {
      await dialog.locator('summary').filter({ hasText: /^补充活动明细/ }).click();
      await dialog.getByRole('combobox', { name: '活动 1 的时间段', exact: true }).focus();
      await page.keyboard.press('Enter');
      const hour = dialog.getByRole('listbox', { name: '开始', exact: true }).getByRole('option', { name: '19:00', exact: true });
      await hour.focus();
      await page.keyboard.press('Enter');
      await page.keyboard.press('Escape');
    }
    await page.evaluate(() => window.dailyExperienceFixture.releaseDates());
    await dialog.getByRole('button', { name: '打开这一天的记录', exact: true }).waitFor();
    assert.equal(await page.getByRole('dialog', { name: '每日行为记录', exact: true }).isVisible(), true, `${scenario}: late server response must not replace a keyboard draft with today's saved record`);
    if (scenario === 'score') {
      assert.equal(await dialog.getByRole('radio', { name: `${SUMMARY[0]} 5`, exact: true }).getAttribute('aria-checked'), 'true');
      assert.equal(await dialog.locator('[role="radio"][aria-checked="true"]').count(), 1);
    } else {
      assert.match(await dialog.getByRole('combobox', { name: '活动 1 的时间段', exact: true }).innerText(), /19:00.*待选结束/);
    }
    await closeDialog(page);
  }
  await page.evaluate(() => { window.dailyExperienceFixture.holdDate = true; });
  const dialog = await openRecord(page);
  await page.waitForFunction(() => window.dailyExperienceFixture.pendingDates.length > 0);
  await dialog.getByRole('button', { name: '趋势与历史', exact: true }).click();
  await page.getByRole('dialog', { name: '趋势与历史记录', exact: true }).getByRole('heading', { name: '近期变化', exact: true }).waitFor();
  await page.evaluate(() => window.dailyExperienceFixture.releaseDates());
  await page.waitForFunction(() => window.dailyExperienceFixture.pendingDates.length === 0);
  // Wait two render frames so the released request's React state updates run.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.getByRole('dialog', { name: '趋势与历史记录', exact: true }).isVisible(), true, 'a delayed date response must not navigate history back to the edit form');
  await closeDialog(page);
}

async function verifyEditRetainsOptional(page) {
  const history = await openHistory(page);
  const article = history.locator('article').filter({ hasText: /2020年1月2日/ });
  await article.getByRole('button', { name: /2020年1月2日/ }).click();
  await article.getByRole('button', { name: '修改这份记录', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '修改行为记录', exact: true });
  await dialog.waitFor();
  assert.equal(await dialog.getByRole('textbox', { name: '活动 1 的内容', exact: true }).inputValue(), '晚饭后散步十分钟');
  assert.equal(await dialog.getByRole('textbox', { name: '活动 1 的备注', exact: true }).inputValue(), '保留的活动备注');
  assert.equal(await dialog.getByRole('radio', { name: '做完活动后的心情 0', exact: true }).getAttribute('aria-checked'), 'true');
  await dialog.locator('summary').filter({ hasText: /^其他感受（4 项选填）/ }).click();
  for (const [label, score] of [['成就', 0], ['联结', 2], ['重要', 5]]) {
    assert.equal(await dialog.getByRole('radio', { name: `${label} ${score}`, exact: true }).getAttribute('aria-checked'), 'true');
  }
  const enjoyment = dialog.getByRole('radiogroup', { name: '愉悦（0 到 5）', exact: true });
  await enjoyment.waitFor();
  assert.equal(await enjoyment.getByRole('radio').count(), 6);
  assert.equal(await enjoyment.getByRole('radio', { checked: true }).count(), 0, 'null optional scores remain unselected');
  const help = dialog.getByRole('button', { name: '解释：成就', exact: true });
  await help.focus();
  await page.keyboard.press('Enter');
  await page.getByRole('dialog', { name: '成就', exact: true }).waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await dialog.isVisible(), true);
  await dialog.locator('summary').filter({ hasText: /^其他感受（4 项选填）/ }).click();
  await dialog.getByRole('textbox', { name: '想再写一点（可留空）', exact: true }).fill('只修改整天感受，保留所有活动分数。');
  const save = dialog.getByRole('button', { name: '保存修改', exact: true });
  await enabled(save);
  await save.click();
  const saved = page.getByRole('dialog', { name: '记录已保存', exact: true });
  await saved.waitFor();
  const { write, record } = await page.evaluate(() => ({ write: window.dailyExperienceFixture.writes.at(-1), record: window.dailyExperienceFixture.lastSaved }));
  assert.equal(write.method, 'PUT');
  assert.equal(write.path, '/api/assessment/2');
  assert.equal(write.body.expected_revision, 7);
  assert.equal(write.body.activities[0].emotion, 0);
  assert.equal(write.body.activities[0].achievement, 0);
  assert.equal(write.body.activities[0].connection, 2);
  assert.equal(write.body.activities[0].enjoyment, null);
  assert.equal(write.body.activities[0].importance, 5);
  assert.equal(write.body.activities[0].note, '保留的活动备注');
  assert.equal(record.revision_no, 8);
  await saved.getByRole('button', { name: '完成，返回对话', exact: true }).click();
  await page.locator('dialog[open]').waitFor({ state: 'hidden' });
  const again = await openHistory(page);
  const revised = again.locator('article').filter({ hasText: /2020年1月2日/ });
  await revised.getByRole('button', { name: /2020年1月2日/ }).click();
  await revised.getByText('只修改整天感受，保留所有活动分数。', { exact: true }).waitFor();
  await revised.getByRole('button', { name: '修改这份记录', exact: true }).click();
  await page.getByRole('dialog', { name: '修改行为记录', exact: true }).getByRole('button', { name: '保存修改', exact: true }).click();
  await page.getByRole('dialog', { name: '记录已保存', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => window.dailyExperienceFixture.writes.at(-1).body.expected_revision), 8, 'next edit uses the revision returned by the previous save');
  await page.getByRole('button', { name: '完成，返回对话', exact: true }).click();
}

async function verifyFirstRecordStates(page, width) {
  await page.evaluate(() => { window.dailyExperienceFixture.records = []; });
  await page.getByRole('region', { name: '每日记录快捷入口' }).getByRole('button', { name: '历史与趋势', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: '趋势与历史记录', exact: true });
  await dialog.getByRole('heading', { name: '还没有历史记录', exact: true }).waitFor();
  assert.equal(await dialog.getByRole('img').count(), 0, 'empty history must not invent chart data');
  await assertNoOverflow(page, `empty history ${width}`);
  await page.screenshot({ path: path.join(OUT, `history-empty-${width}.png`), fullPage: true, animations: 'disabled' });
  await closeDialog(page);
  await page.evaluate(() => {
    const f = window.dailyExperienceFixture;
    f.records = [{ ...f.lastSaved, scale_version: 2, status: 'completed', overall_mood: 0 }];
  });
  dialog = await openHistory(page);
  const trends = dialog.getByRole('region', { name: '近期变化', exact: true });
  await trends.getByText('目前只有 1 次整体心情评分，先保留这个点，之后可以对照更多记录。', { exact: true }).waitFor();
  assert.equal(await trends.locator('circle').count(), 1);
  assert.match(await trends.locator('circle title').textContent(), /整体心情 0\/5$/);
  assert.deepEqual((await trends.locator('path').getAttribute('d')).match(/[ML]/g), ['M'], 'one record produces one point without implying a trend');
  await assertNoOverflow(page, `single record ${width}`);
  await page.screenshot({ path: path.join(OUT, `history-single-${width}.png`), fullPage: true, animations: 'disabled' });
  await closeDialog(page);
}

exports.installFixtures = installFixtures;

exports.run = async function run(browser, baseUrl = process.env.BASE_URL || 'http://127.0.0.1:3121') {
  await fs.mkdir(OUT, { recursive: true });
  const results = [];
  for (const width of [1440, 390, 360]) {
    const context = await browser.newContext({
      viewport: { width, height: width < 600 ? 844 : 900 }, timezoneId: 'Asia/Shanghai',
      isMobile: width < 600, hasTouch: width < 600, serviceWorkers: 'block',
      reducedMotion: 'reduce',
    });
    const escapedApis = [];
    await context.route('**/api/**', async route => {
      escapedApis.push({ method: route.request().method(), url: route.request().url() });
      await route.fulfill({ status: 501, contentType: 'application/json', body: '{"detail":"QA network backstop: API access blocked"}' });
    });
    await context.addInitScript(installFixtures);
    const page = await context.newPage();
    page.setDefaultTimeout(12_000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.stack || error.message));
    try {
      await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
      const entry = page.getByRole('region', { name: '每日记录快捷入口', exact: true });
      await entry.getByText('今天暂无记录', { exact: true }).waitFor();
      await page.waitForFunction(() => window.dailyExperienceFixture.calls.some(call => call.path.endsWith('/events')));
      for (const module of ['module_1', 'module_2', 'module_3', 'module_4', 'module_3']) {
        await page.evaluate(module => window.dailyExperienceFixture.setModule(module), module);
        await page.getByText(`模拟对话 ${module}：可以按自己的节奏继续。`, { exact: true }).waitFor();
        assert.equal(await entry.count(), module === 'module_3' ? 1 : 0, `${module}: daily entry is exclusive to Module III`);
        assert.equal(await page.getByRole('button', { name: '每日记录', exact: true }).count(), module === 'module_3' ? 1 : 0, `${module}: header entry follows the same module boundary`);
        if (module === 'module_3') await entry.getByRole('button', { name: '记录今天', exact: true }).waitFor();
        assert.equal(await page.locator('dialog[open]').count(), 0, `${module}: no record modal opens automatically`);
        const composer = page.getByRole('textbox', { name: 'Message', exact: true });
        await composer.fill(`可以继续 ${module} 对话`);
        await enabled(page.getByRole('button', { name: 'Send', exact: true }));
        await composer.fill('');
        await assertNoOverflow(page, `${module} entry ${width}`);
      }
      await openRecord(page);
      await page.evaluate(() => window.dailyExperienceFixture.setModule('module_4'));
      await page.locator('dialog[open]').waitFor({ state: 'hidden' });
      await page.evaluate(() => window.dailyExperienceFixture.setModule('module_3'));
      await entry.getByText('今天暂无记录', { exact: true }).waitFor();
      assert.equal(await page.locator('dialog[open]').count(), 0, 'returning to Module III does not reopen a stale daily dialog');
      await page.screenshot({ path: path.join(OUT, `entry-${width}.png`), fullPage: true });
      await verifyTrends(page, width);
      await verifySummaryOnly(page, width);
      await verifyDelayedReadProtectsKeyboardDraft(page);
      await verifyEditRetainsOptional(page);
      // A transient status read failure leaves the conversation and both
      // entry actions available; retry restores the saved state.
      await page.evaluate(() => {
        window.dailyExperienceFixture.failDate = true;
        window.dispatchEvent(new Event('focus'));
      });
      await entry.getByText('今日状态暂时无法读取', { exact: true }).waitFor();
      await page.getByRole('textbox', { name: 'Message', exact: true }).fill('仍然可以继续对话');
      await enabled(page.getByRole('button', { name: 'Send', exact: true }));
      await page.evaluate(() => { window.dailyExperienceFixture.failDate = false; });
      await entry.getByRole('button', { name: '重试读取今日记录状态', exact: true }).click();
      await entry.getByText('今天已记录', { exact: true }).waitFor();
      await assertNoOverflow(page, `final ${width}`);
      await verifyFirstRecordStates(page, width);
      assert.deepEqual(await page.evaluate(() => window.dailyExperienceFixture.unknown), [], 'every requested API has an explicit synthetic response');
      assert.deepEqual(escapedApis, [], 'no API request escaped the in-page fixture');
      assert.deepEqual(errors, [], 'no browser runtime errors');
      results.push({ width, passed: true, modules: ['M1', 'M2', 'M3', 'M4'], mockedWrites: await page.evaluate(() => window.dailyExperienceFixture.writes.length) });
    } catch (error) {
      await page.screenshot({ path: path.join(OUT, `failure-${width}.png`), fullPage: true }).catch(() => {});
      await fs.writeFile(path.join(OUT, `failure-${width}.json`), JSON.stringify({
        message: error.stack || error.message, errors, escapedApis,
        fixture: await page.evaluate(() => ({ calls: window.dailyExperienceFixture?.calls, unknown: window.dailyExperienceFixture?.unknown })).catch(() => null),
      }, null, 2));
      throw error;
    } finally {
      await context.close();
    }
  }
  await fs.writeFile(path.join(OUT, 'results.json'), JSON.stringify(results, null, 2));
  return results;
};

if (require.main === module) {
  (async () => {
    const { chromium } = require('playwright');
    const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
    try {
      console.log(JSON.stringify(await exports.run(browser, process.argv[2] || process.env.BASE_URL), null, 2));
    } finally {
      await browser.close();
    }
  })().catch(error => { console.error(error); process.exitCode = 1; });
}
