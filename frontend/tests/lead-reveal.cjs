// Phase-aware presentation through the real MessageRow. No model or database writes.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const base = process.argv[2] || 'http://127.0.0.1:3194';
(async () => {
 const browser = await chromium.launch({headless:true,channel:'chrome'});
 const errors=[];
 try {
  const page=await browser.newPage({viewport:{width:390,height:844}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/stream-preview');
  await page.waitForFunction(()=>window.revealFixture);
  await page.waitForTimeout(1100);
  await page.clock.install();
  const run=ms=>page.clock.runFor(ms);
  const reset=async()=>{await page.evaluate(()=>window.revealFixture.reset());await run(32);};
  const update=(text,pending,phase='lead',len=text.length)=>page.evaluate(({text,pending,phase,len})=>window.revealFixture.update(text,pending,phase,len),{text,pending,phase,len});
  const read=()=>page.locator('[data-streaming-reply]').textContent();
  const noDots=async()=>assert.equal(await page.locator('[data-reply-wait], [data-generation-status]').count(),0);
  const lead='听起来昨天的事情让你很疲惫，我们可以慢慢理清当时的感受。';
  await reset(); await update(lead,true); await run(960);
  assert.equal(await read(),'','lead first character waits a full second');
  await run(80);assert.equal(await read(),lead[0],'lead starts just after one second');
  await run(960);
  const first=await read();assert.ok(first.length>=3&&first.length<=8, 'lead must not finish immediately');await noDots();
  await run(6000); const later=await read();assert.ok(later.length>first.length&&later.length<lead.length,'lead keeps revealing during a slow body request');await noDots();
  await run(16000);assert.ok((await read()).length<lead.length,'reserve the closing character until body arrives');
  assert.equal(await page.locator('[data-lead-status]').count(),1,'long wait is explained honestly');
  await page.screenshot({path:'/tmp/lead-slow-mobile.png'});
  const body='\n\n我们先看看最让你觉得累的是哪一部分。';
  await update(lead+body,true,'body',lead.length);await run(350);
  assert.ok((await read()).includes('我们先'),'body gets through within 350ms');
  assert.equal(await page.locator('[data-lead-cursor], [data-lead-status]').count(),0);
  await update(lead+body,false,'body',lead.length);await run(1500);
  assert.equal(await read(),lead+body.replace(/\n/g,''));
  // Frequent incoming chunks cannot repeatedly reset the lead's reveal timer.
  await reset();
  for(let i=1;i<=lead.length;i++){
   await update(lead.slice(0,i),true,'lead',i);await run(120);
   if(i===8)assert.equal(await read(),'','incoming chunks must respect the initial delay');
   if(i===9)assert.equal(await read(),lead[0],'incoming chunks must not restart the delay');
  }
  assert.ok((await read()).length<lead.length/2,'SSE chunk rate must not become display rate');await noDots();
  await update(lead+body,false,'body',lead.length);await run(350);
  assert.ok((await read()).length > lead.length, 'first body character must appear within 350ms: '+JSON.stringify(await read()));
  await run(1600);assert.equal(await read(),lead+body.replace(/\n/g,''));
  // An early body or terminal event must not wait for the lead's start delay.
  await reset();await update(lead,true);await run(200);
  await update(lead+body,true,'body',lead.length);await run(350);
  assert.ok((await read()).length>lead.length,'early body bypasses the lead delay');
  await reset();await update('好。',true);await run(200);
  await update('好。',false);await run(32);assert.equal(await read(),'好。');
  // During a long wait, even the slowest lead pacing advances within 500ms
  // (plus one display frame), as long as more than the closing character remains.
  await reset();const longLead=lead.repeat(8);await update(longLead,true);await run(16000);
  for(let i=0;i<3;i++){
   const before=(await read()).length;await run(520);
   assert.ok((await read()).length>before,'slowest lead interval stays below 520ms including frame rounding');
  }
  // Short lead and slow body; terminal stop/error must release held characters.
  await reset();await update('好。',true);await run(25000);
  assert.equal(await read(),'好');await noDots();
  await update('好。',false);await run(32);assert.equal(await read(),'好。');
  assert.equal(await page.locator('[data-lead-cursor], [data-lead-status]').count(),0);
  // Emoji/combining sequences stay whole even during accelerated handoff.
  await reset();const emoji='我在这里👨‍👩‍👧‍👦，我们慢慢聊e\u0301。';await update(emoji,true);await run(1000);
  await update(emoji+body,false,'body',emoji.length);await run(1800);
  assert.equal(await read(),emoji+body.replace(/\n/g,''));
  // Reduced motion and loaded history never conceal text behind an animation.
  await page.emulateMedia({reducedMotion:'reduce'});
  await reset();await update(lead,true);await run(32);assert.equal(await read(),lead);await noDots();
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.evaluate(text=>window.revealFixture.reset(text,false),lead);await run(32);
  assert.equal(await read(),lead);assert.equal(await page.locator('[data-lead-cursor]').count(),0);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);
  console.log('PASS: 1s lead start; chunk-stable delay; 500ms slowest pacing; early-body/stop bypass; burst/chunk lead pacing; slow/short leads; no waiting dots; <=350ms body handoff; Unicode; reduced motion; history; mobile');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
