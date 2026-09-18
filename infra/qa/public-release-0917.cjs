// Public-page checks only. Never submit a form or create production test data.
const { chromium } = require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const out = path.resolve('.test-tmp/public-release-0917');
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, isMobile: width < 600, hasTouch: width < 600 });
      const page = await context.newPage();
      const errors = [], mutations = [], assetFailures = [];
      page.on('pageerror', e => errors.push(e.message));
      page.on('response', r => { if (r.url().includes('/_next/') && r.status() >= 400) assetFailures.push(r.status()); });
      await context.route('**/api/**', async route => {
        if (!['GET', 'HEAD', 'OPTIONS'].includes(route.request().method())) {
          mutations.push(route.request().method());
          return route.abort();
        }
        return route.continue();
      });
      const response = await page.goto('https://bacoach.xyz/', { waitUntil: 'networkidle' });
      if (response.status() !== 200) throw Error('Homepage not 200');
      await page.getByRole('heading', { name: '欢迎回来' }).waitFor();
      await page.screenshot({ path: path.join(out, `login-${width}.png`), scale: 'css' });
      await page.getByRole('button', { name: '注册', exact: true }).click();
      const date = page.locator('input[type=date]');
      await date.scrollIntoViewIfNeeded();
      if (await date.inputValue()) throw Error('Birthday must start empty');
      if (await date.getAttribute('required') === null) throw Error('Birthday must be required');
      await page.screenshot({ path: path.join(out, `birthday-${width}.png`), scale: 'css' });
      // Date ranges are validated by the API, not a native max attribute.
      // Do not submit a production registration just to repeat isolated tests.
      const fit = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
      if (fit.scrollWidth > fit.width) throw Error('Horizontal overflow');
      await page.getByRole('button', { name: '登录', exact: true }).first().click();
      await page.getByRole('heading', { name: '欢迎回来' }).waitFor();
      if (errors.length || mutations.length || assetFailures.length) throw Error(JSON.stringify({ errors, mutations, assetFailures }));
      console.log(JSON.stringify({ width, status: 200, birthdayRequired: true, birthdayEmpty: true, dateRangeCheck: 'isolated_backend_tests_only', fit, errors, mutations, assetFailures }));
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
