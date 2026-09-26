// UI-only archive regression: all API calls use synthetic data.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { install } = require('./goal-ui-fixture.cjs');

exports.run = async function(browser, baseUrl = 'http://127.0.0.1:3026') {
  const out = path.resolve(__dirname, '../../.test-tmp/goal-support-0926');
  await fs.mkdir(out, { recursive: true });
  const reports = [];
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, isMobile: width < 600, hasTouch: width < 600 });
    try {
      const fixture = await install(context);
      await context.route('**/api/auth/me', route => route.fulfill({ json: {
        username: 'archive-qa', nickname: '本地验收', profile_uuid: 'qa', role: 'admin',
        email_verified: true, email_required: false, current_module: 'module_2', display_id: 'test#1000'
      } }));
      const common = { activity_content: '饭后散步', schedule_text: '一周一到两次饭后',
        duration_minutes: 10, location: '公司附近', record_status: 'draft', confirmation_status: 'unconfirmed',
        companion: null, resources: null, created_at: '2026-09-26T08:00:00Z', updated_at: '2026-09-26T08:00:00Z' };
      const barriers = '不想动；很难坚持';
      const coping = '困难：不想动，很难坚持；应对：先从每次十分钟开始，习惯后再慢慢增加';
      await context.route('**/api/program/goals/*/history?*', route => route.fulfill({ json: {
        goal: fixture.overview.goals[0], page: 1, page_size: 12,
        totals: { plans: 3, cycles: 0, activities: 0 }, cycles: [], activities: [], plans: [
          { ...common, id: 'current', version_no: 3, potential_barriers: ['不想动', '很难坚持'],
            barrier_coping_plan: [{ barrier: '不想动，很难坚持', plan: '先从每次十分钟开始，习惯后再慢慢增加' }],
            difficulty_rating: 0, difficulty: '旧的文字描述不应覆盖评分' },
          { ...common, id: 'legacy', version_no: 2, potential_barriers: [], barrier_coping_plan: [],
            difficulty_rating: null, difficulty: '有一点难' },
          { ...common, id: 'unknown', version_no: 1, potential_barriers: null, barrier_coping_plan: null,
            difficulty_rating: null, difficulty: null }
        ]
      } }));
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(baseUrl);
      const entry = page.getByRole('button', { name: '我的目标', exact: true });
      await page.getByRole('button', { name: '切换对话侧栏', exact: true }).waitFor();
      if (!await entry.isVisible()) await page.getByRole('button', { name: '切换对话侧栏', exact: true }).click();
      await entry.click();
      const dialog = page.getByRole('dialog', { name: '我的目标', exact: true });
      await dialog.locator('[data-goal-id="g1"]').click();
      const card = dialog.locator('article').filter({ has: page.getByRole('heading', { name: 'PA 目标卡 · 第 3 版', exact: true }) });
      await card.waitFor();
      const field = (scope, label) => scope.locator('dl > div').filter({ has: page.locator('dt').filter({ hasText: new RegExp(`^${label}$`) }) }).locator('dd');
      assert.equal(await card.locator('details').evaluate(node => node.open), false);
      for (const [label, value] of [['可能遇到的困难', barriers], ['应对办法', coping], ['难度感受', '0 / 10'], ['同行者', '尚未记录']]) {
        const valueNode = field(card, label);
        assert.equal(await valueNode.innerText(), value);
        assert.equal(await valueNode.isVisible(), true);
        assert.equal(await valueNode.evaluate(node => node.closest('details') === null), true);
      }
      assert.equal(await card.getByText('同行 / 支持', { exact: true }).count(), 0);
      await field(card, '应对办法').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(out, `core-plan-${width}.png`), scale: 'css' });
      await card.locator('summary').click();
      assert.equal(await field(card, '可用支持').innerText(), '尚未记录');
      const legacy = dialog.locator('article').filter({ has: page.getByRole('heading', { name: 'PA 目标卡 · 第 2 版', exact: true }) });
      assert.equal(await field(legacy, '难度感受').innerText(), '有一点难');
      const unknown = dialog.locator('article').filter({ has: page.getByRole('heading', { name: 'PA 目标卡 · 第 1 版', exact: true }) });
      for (const label of ['可能遇到的困难', '应对办法', '难度感受']) assert.equal(await field(unknown, label).innerText(), '尚未记录');
      const fit = await dialog.evaluate(node => ({ width: node.clientWidth, scrollWidth: node.scrollWidth }));
      assert.ok(fit.scrollWidth <= fit.width + 1);
      assert.deepEqual(errors, []);
      assert.equal(fixture.requests.filter(request => request.method !== 'GET').length, 0);
      reports.push({ width, corePlanVisibleWithoutExpanding: true, optionalSupportNotInvented: true,
        numericZeroPreserved: true, legacyDifficultyPreserved: true, unknownPreserved: true, fit, errors });
    } finally { await context.close(); }
  }
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(reports, null, 2));
  return reports;
};

if (require.main === module) (async () => {
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try { console.log(JSON.stringify(await exports.run(browser, process.argv[2]), null, 2)); }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
