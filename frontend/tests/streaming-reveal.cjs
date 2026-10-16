// In an isolated frontend copy, put tests/fixtures/StreamingReveal.tsx at
// app/stream-preview/page.tsx and start Next. Then run this script with its URL.
// The fixture exercises the real MessageRow, hook, Markdown and CSS.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const base = process.argv[2] || 'http://127.0.0.1:3184';
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 760 } });
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base + '/stream-preview');
    await page.waitForFunction(() => window.revealFixture);
    await page.waitForTimeout(1000); // Let the demo's one-shot starter finish.
    const reset = async (text = '', busy = true) => {
      await page.evaluate(({ text, busy }) => window.revealFixture.reset(text, busy), { text, busy });
      await page.waitForTimeout(60);
    };
    const update = (text, busy) => page.evaluate(({ text, busy }) => window.revealFixture.update(text, busy), { text, busy });
    const finish = text => page.waitForFunction(expected => document.querySelector('[data-streaming-reply]')?.textContent === expected && document.querySelector('[data-streaming-reply]')?.getAttribute('aria-busy') === 'false', text);

    await reset();
    const text = '今天可以从轻松的一小步开始，留意自己的身体和心情，选择适合自己的节奏。';
    await page.evaluate(() => {
      window.revealSamples = [];
      window.sampleObserver = new MutationObserver(() => {
        const el = document.querySelector('[data-streaming-reply]');
        if (el) window.revealSamples.push(el.textContent);
      });
      window.sampleObserver.observe(document.querySelector('[data-fixture-room]'), { subtree: true, childList: true, characterData: true });
    });
    await update(text, false); // done arrives alongside a large delta
    await page.waitForFunction(() => document.querySelectorAll('.reply-character').length > 2);
    const mid = await page.evaluate(() => ({
      content: document.querySelector('[data-streaming-reply]').textContent,
      opacity: Number(getComputedStyle(document.querySelector('.reply-character:last-child')).opacity),
      animation: getComputedStyle(document.querySelector('.reply-character:last-child')).animationName,
    }));
    assert.ok(mid.content.length < text.length, 'a completed burst must still play progressively');
    assert.equal(mid.animation, 'reply-character-reveal');
    assert.ok(mid.opacity < 1, 'new characters visibly fade in');
    await finish(text);
    const samples = await page.evaluate(() => { window.sampleObserver.disconnect(); return [...new Set(window.revealSamples)]; });
    assert.ok(samples.length > 20, 'a burst is displayed as many individual characters');
    for (let i = 1; i < samples.length; i++) assert.ok(samples[i].length - samples[i - 1].length <= 1, 'never jump ahead by a chunk');

    await reset();
    await update('开头', true);
    await page.waitForFunction(() => document.querySelector('[data-streaming-reply]')?.textContent === '开头');
    await update('开头，然后慢慢补上后面的内容。', true);
    await update('开头，然后慢慢补上后面的内容。结束。', false);
    await finish('开头，然后慢慢补上后面的内容。结束。');

    await reset();
    const unicode = '你好👨‍👩‍👧‍👦，e\u0301，🌿。';
    await update(unicode, false); await finish(unicode);
    const chars = await page.locator('.reply-character').allTextContents();
    assert.ok(chars.includes('👨‍👩‍👧‍👦') && chars.includes('e\u0301'));

    await reset('历史回复立即显示。', false);
    assert.equal(await page.locator('[data-streaming-reply]').textContent(), '历史回复立即显示。');
    assert.equal(await page.locator('.reply-character').count(), 0);
    await reset(); await update('这是一段尚未显示完毕的很长的回复。'.repeat(8), true);
    await page.waitForTimeout(100);
    await update('服务器修正后的最终回复。', false);
    assert.equal(await page.locator('[data-streaming-reply]').textContent(), '服务器修正后的最终回复。');
    await reset('另一个会话的历史。', false);
    await page.waitForTimeout(200);
    assert.equal(await page.locator('[data-streaming-reply]').textContent(), '另一个会话的历史。');

    await page.emulateMedia({ reducedMotion: 'reduce' });
    await reset(); await update(text, false); await finish(text);
    assert.equal(await page.locator('.reply-character').count(), 0);
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await reset(); await update('一'.repeat(200), true); await page.waitForTimeout(100);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.waitForFunction(() => document.querySelector('[data-streaming-reply]')?.textContent.length === 200);
    await page.emulateMedia({ reducedMotion: 'no-preference' });

    await reset();
    await update('- 第一点\n- 第二点', true);
    await page.waitForFunction(() => document.querySelectorAll('[data-streaming-reply] li').length === 2 && document.querySelector('[data-streaming-reply]')?.textContent.includes('第二点'));
    await page.evaluate(() => { window.firstItem = document.querySelector('[data-streaming-reply] li'); });
    await update('- 第一点\n- 第二点\n- 第三点', false);
    await finish('第一点第二点第三点');
    assert.equal(await page.evaluate(() => window.firstItem === document.querySelector('[data-streaming-reply] li')), true, 'existing list items must not remount/replay');

    await page.setViewportSize({ width: 390, height: 844 });
    await reset();
    await update('**慢慢来**\n\n保持自然换行，选择自己觉得舒服的节奏。🌿', false);
    await finish('慢慢来保持自然换行，选择自己觉得舒服的节奏。🌿');
    assert.equal(await page.locator('[data-streaming-reply] strong').textContent(), '慢慢来');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    assert.deepEqual(errors, []);
    console.log('PASS: per-character burst, fade opacity, drain after done, multiple deltas, Unicode, history, correction, room reset, reduced motion, stable lists, mobile Markdown');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
