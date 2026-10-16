// Synthetic delayed streaming and transient read failures; no real API calls.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { installFixtures } = require('./goal-formulation-1003.cjs');
const OUT = path.resolve(__dirname, '../../.test-tmp/card-daily-refresh');

function delayedFixture() {
  const f = window.goalCardFixture;
  Object.assign(f, { dailyReads: 0, dailyFailures: 100, dailyStatus: 503, dailyMode: 'http', confirmPosts: 0, cardReads: 0, streamOpen: false });
  const original = window.fetch.bind(window);
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.pathname.endsWith('/goal-card') && (options.method || 'GET') === 'GET') f.cardReads++;
    if (url.pathname === '/api/assessment/by-date') {
      f.dailyReads++;
      if (f.dailyFailures > 0) {
        f.dailyFailures--;
        if (f.dailyMode === 'network') throw new TypeError('Synthetic network failure');
        if (f.dailyMode === 'stall') return new Promise((resolve,reject) => {
          if (options.signal.aborted) reject(options.signal.reason);
          else options.signal.addEventListener('abort',()=>reject(options.signal.reason),{once:true});
        });
        if (f.dailyMode === 'malformed') return new Response('not-json',{status:200});
        return new Response('{"detail":"synthetic temporary failure"}', {status:f.dailyStatus});
      }
      if (f.holdDailyRead) return new Promise(resolve=>{f.releaseDailyRead=()=>resolve(new Response('null',{status:200}));});
    }
    if (url.pathname === '/api/chat/stream') {
      const body = JSON.parse(options.body);
      if (body.metadata?.goal_card_action === 'confirm') {
        f.confirmPosts++; f.card = { ...f.card, phase: 'confirmed', revision: f.card.revision + 1 };
        f.module = 'module_3'; f.revision++; f.streamOpen = true;
        const userId = 11 + f.messages.length;
        f.messages.push({ id: userId, role: 'user', content: body.message });
        const frame = (event, data) => new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
        return new Response(new ReadableStream({ start(controller) {
          controller.enqueue(frame('meta', {session_id:'goal-card-ui', user_message_id:userId, reply_module:'module_3', next_module:'module_3'}));
          controller.enqueue(frame('delta', {text:'这个安排已经保存。我们继续聊。'}));
          f.finishConfirm = () => { if (!f.streamOpen) return; f.streamOpen = false;
            f.messages.push({id:userId+1, reply_to_message_id:userId, role:'assistant',content:'这个安排已经保存。我们继续聊。'});
            controller.enqueue(frame('persisted',{saved:true}));controller.close();f.broadcast(); };
        }, cancel() { f.streamOpen=false; } }), {headers:{'Content-Type':'text/event-stream'}});
      }
    }
    return original(input, options);
  };
}

exports.run = async (browser, base='http://127.0.0.1:3124', baseline=false) => {
 await fs.mkdir(OUT,{recursive:true}); const results=[];
 for (const width of [1440,390,360]) {
  const context=await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce'});
  const role = width === 390 ? 'user' : 'admin';
  await context.addInitScript(installFixtures, {role, settings:{routing_mode:'router_only',reply_mode:'ack_deep',reply_effort:'low',knowledge_mediator_enabled:true}});await context.addInitScript(delayedFixture);
  const page=await context.newPage();const errors=[],escaped=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',route=>{escaped.push(route.request().url());return route.fulfill({status:599,body:'Real API forbidden'});});
  try {
   await page.goto(base); const panel=page.getByRole('region',{name:'目标卡快捷入口',exact:true});
   await panel.getByText('先聊聊你的想法，开始制定目标时，卡片会在这里展开。',{exact:true}).waitFor();
   await page.evaluate(()=>{const f=window.goalCardFixture;f.card={id:'qa-ready',kind:'primary',phase:'ready',revision:6,fields:{activity_content:'QA 合成散步'},concerns:[]};f.broadcast();});
   const confirm=panel.getByRole('button',{name:'确认这个安排',exact:true});await confirm.waitFor();
   await confirm.dblclick();
   await page.getByText('这个安排已经保存。我们继续聊。',{exact:true}).waitFor();
   const daily=page.getByRole('region',{name:'每日记录快捷入口',exact:true});await daily.waitFor();
   await daily.getByText('今日状态暂时无法读取',{exact:true}).waitFor();
   const failedReads=await page.evaluate(()=>{const f=window.goalCardFixture;f.dailyFailures=0;return f.dailyReads;});
   if (baseline) {
    await page.waitForTimeout(5500);
    assert.equal(await panel.getByText('等待你确认',{exact:true}).count(),1);
    assert.equal(await daily.getByText('今日状态暂时无法读取',{exact:true}).count(),1);
    assert.equal(await page.evaluate(()=>window.goalCardFixture.dailyReads),failedReads);
   } else {
    await panel.getByText('已保存',{exact:true}).waitFor({timeout:5000});
    await daily.getByText('今天暂无记录',{exact:true}).waitFor({timeout:5000});
    assert.equal(await page.evaluate(()=>window.goalCardFixture.streamOpen),true,'card commits visible before stream completes');
    assert.equal(await page.evaluate(()=>window.goalCardFixture.dailyReads),failedReads+1,'one transient read recovers automatically');
   }
   assert.equal(await page.evaluate(()=>window.goalCardFixture.confirmPosts),1,'double click cannot duplicate confirmation');
   assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);
   await page.screenshot({path:path.join(OUT,`${baseline?'baseline':'fixed'}-${width}.png`),fullPage:true});
   await page.evaluate(()=>window.goalCardFixture.finishConfirm());
   if (!baseline && width === 1440) {
    // Persistent temporary failures stay visible and exhaust a bounded budget.
    await page.evaluate(()=>{const f=window.goalCardFixture;f.dailyFailures=100;window.dispatchEvent(new Event('focus'));});
    await daily.getByText('今日状态暂时无法读取',{exact:true}).waitFor();
    const firstFailure=await page.evaluate(()=>window.goalCardFixture.dailyReads);
    await page.waitForFunction(n=>window.goalCardFixture.dailyReads>=n+2,firstFailure,{timeout:7000});
    await page.waitForTimeout(1200);
    assert.equal(await page.evaluate(()=>window.goalCardFixture.dailyReads),firstFailure+2,'only two automatic retries');
    assert.equal(await daily.getByText('今日状态暂时无法读取',{exact:true}).count(),1);
    await page.evaluate(()=>{window.goalCardFixture.dailyFailures=0;});
    await daily.getByRole('button',{name:'重试读取今日记录状态'}).click();
    await daily.getByText('今天暂无记录',{exact:true}).waitFor();
    // Permission and malformed-response errors are not silently retried.
    for (const mode of ['forbidden','malformed']) {
     await page.evaluate(mode=>{const f=window.goalCardFixture;f.dailyFailures=100;f.dailyStatus=403;f.dailyMode=mode;window.dispatchEvent(new Event('focus'));},mode);
     await daily.getByText('今日状态暂时无法读取',{exact:true}).waitFor();
     const reads=await page.evaluate(()=>window.goalCardFixture.dailyReads);
     await page.waitForTimeout(4300);
     assert.equal(await page.evaluate(()=>window.goalCardFixture.dailyReads),reads,`${mode} not retried`);
     await page.evaluate(()=>{window.goalCardFixture.dailyFailures=0;});
     await daily.getByRole('button',{name:'重试读取今日记录状态'}).click();
     await daily.getByText('今天暂无记录',{exact:true}).waitFor();
    }
    for (const mode of ['network','stall']) {
     await page.evaluate(mode=>{const f=window.goalCardFixture;f.dailyFailures=1;f.dailyMode=mode;window.dispatchEvent(new Event('focus'));},mode);
     await daily.getByText('今日状态暂时无法读取',{exact:true}).waitFor({timeout:11000});
     await daily.getByText('今天暂无记录',{exact:true}).waitFor({timeout:5000});
    }
    // Focus + visibility events share an in-flight request, without cancellation.
    await page.evaluate(()=>{const f=window.goalCardFixture;f.holdDailyRead=true;window.dispatchEvent(new Event('focus'));});
    await page.waitForFunction(()=>typeof window.goalCardFixture.releaseDailyRead==='function');
    const pendingReads=await page.evaluate(()=>{window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));return window.goalCardFixture.dailyReads;});
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(()=>window.goalCardFixture.dailyReads),pendingReads);
    await page.evaluate(()=>{const f=window.goalCardFixture;f.holdDailyRead=false;f.releaseDailyRead();});
    await daily.getByText('今天暂无记录',{exact:true}).waitFor();
    // An obsolete day's held response must not mark the new day completed.
    await page.clock.install({time:new Date('2026-10-03T23:59:59')});
    await page.evaluate(()=>{const f=window.goalCardFixture;f.holdDailyRead=true;window.dispatchEvent(new Event('focus'));});
    await page.waitForTimeout(100);
    await page.clock.setSystemTime(new Date('2026-10-04T00:00:01'));
    const rolloverReads=await page.evaluate(()=>{const f=window.goalCardFixture;f.holdDailyRead=false;f.releaseDailyRead();return f.dailyReads;});
    await page.waitForFunction(n=>window.goalCardFixture.dailyReads>n,rolloverReads);
    await daily.getByText('今天暂无记录',{exact:true}).waitFor();
    // Unmount clears retry timers; a hidden module cannot keep retrying reads.
    await page.evaluate(()=>{const f=window.goalCardFixture;f.dailyFailures=100;f.dailyStatus=503;f.dailyMode='http';window.dispatchEvent(new Event('focus'));});
    await daily.getByText('今日状态暂时无法读取',{exact:true}).waitFor();
    await page.evaluate(()=>{const f=window.goalCardFixture;f.module='module_2';f.broadcast();});
    await daily.waitFor({state:'hidden'});
    const closedReads=await page.evaluate(()=>window.goalCardFixture.dailyReads);
    await page.clock.runFor(5000);
    assert.equal(await page.evaluate(()=>window.goalCardFixture.dailyReads),closedReads,'unmounted entry stops retries');
   }
   results.push({width,role,routingMode:'router_only',replyMode:'ack_deep',effort:'low',newArchitecture:false,baseline,passed:true});
  } catch(error) { await page.screenshot({path:path.join(OUT,`failure-${width}.png`),fullPage:true}).catch(()=>{}); throw error; } finally {await context.close();}
 }
 await fs.writeFile(path.join(OUT,baseline?'baseline.json':'results.json'),JSON.stringify(results,null,2));return results;
};
if(require.main===module)(async()=>{const {chromium}=require('playwright');const browser=await chromium.launch({headless:true,channel:'chrome'});try{console.log(JSON.stringify(await exports.run(browser,process.argv[2],process.argv.includes('--baseline')),null,2));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
