// Local UI only. All API traffic is mocked and a network backstop forbids writes.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { installFixtures } = require('./daily-experience-1002.cjs');
const OUT = path.resolve(__dirname, '../../.test-tmp/daily-sidebar');
exports.run = async (browser, baseUrl) => {
  await fs.mkdir(OUT, { recursive: true });
  const results = [];
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
    const escaped = [], errors = [];
    await context.route('**/api/**', async route => {
      escaped.push(route.request().url());
      await route.fulfill({ status: 501, body: '{}' });
    });
    await context.addInitScript(installFixtures, { role: 'admin' });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.setDefaultTimeout(12000);
    try {
      await page.goto(baseUrl);
      await page.getByText('模拟对话 module_3：可以按自己的节奏继续。', { exact: true }).waitFor();
      for (const module of ['module_1', 'module_2', 'module_3', 'module_4']) {
        await page.evaluate(module => window.dailyExperienceFixture.setModule(module), module);
        await page.getByText(`模拟对话 ${module}：可以按自己的节奏继续。`, { exact: true }).waitFor();
        if (width < 600) await page.getByRole('button', { name: '切换对话侧栏', exact: true }).click();
        const sidebar = page.getByRole('navigation', { name: '对话历史', exact: true });
        const daily = sidebar.getByRole('button', { name: '每日记录', exact: true });
        assert.equal(await daily.count(), module === 'module_3' ? 1 : 0, `${width}/${module}: sidebar follows actual availability`);
        if (module === 'module_3') {
          await daily.click();
          const dialog = page.locator('dialog[open]');
          await dialog.waitFor();
          await page.screenshot({ path: path.join(OUT, `opened-${width}.png`) });
          await dialog.getByRole('button', { name: /关闭/ }).first().click();
          await dialog.waitFor({ state: 'hidden' });
        } else if (width < 600) {
          await page.getByRole('button', { name: '关闭侧栏', exact: true }).click();
        }
      }
      await page.reload();
      await page.getByText('模拟对话 module_3：可以按自己的节奏继续。', { exact: true }).waitFor();
      if (width < 600) await page.getByRole('button', { name: '切换对话侧栏', exact: true }).click();
      await page.getByRole('navigation', { name: '对话历史', exact: true }).getByRole('button', { name: '每日记录', exact: true }).click();
      await page.locator('dialog[open]').waitFor();
      assert.deepEqual(escaped, []);
      assert.deepEqual(errors, []);
      results.push({ width, modules: ['M1', 'M2', 'M3', 'M4'], refreshOpens: true, passed: true });
    } catch (error) {
      await page.screenshot({ path: path.join(OUT, `failure-${width}.png`) }).catch(() => {});
      throw error;
    } finally { await context.close(); }
  }
  await fs.writeFile(path.join(OUT, 'results.json'), JSON.stringify(results, null, 2));
  return results;
};
if (require.main === module) (async () => {
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try { console.log(JSON.stringify(await exports.run(browser, process.argv[2] || 'http://127.0.0.1:3137'), null, 2)); }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
