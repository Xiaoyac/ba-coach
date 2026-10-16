// Production assets, synthetic APIs only; no participant records or model calls.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { installFixtures } = require('./goal-formulation-1003.cjs');
const base = process.argv[2] || 'https://bacoach.xyz';
(async () => {
 const browser = await chromium.launch({channel:'chrome',headless:true}); const results=[];
 try { for (const width of [1440,390]) {
  const context = await browser.newContext({viewport:{width,height:900},serviceWorkers:'block'});
  await context.addInitScript(({fixture})=>{
   (0,eval)(`(${fixture})`)({role:'user'});
   const original=window.fetch;
   window.fetch=async(input,options={})=>{
    const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
    if(p==='/api/chat/stream') {
     const body=JSON.parse(options.body), f=window.goalCardFixture;
     const text='可以先从轻松的一小步开始，留意身体和心情的变化，选择自己觉得舒服的节奏。🌿';
     f.messages.push({id:101,role:'user',content:body.message},{id:102,role:'assistant',reply_to_message_id:101,content:text}); f.revision++;
     return new Response(new ReadableStream({start(controller){
      const send=(event,data)=>controller.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
      send('meta',{session_id:'goal-card-ui',user_message_id:101,reply_module:'module_2',next_module:'module_2'});
      setTimeout(()=>{send('delta',{text});send('done',{});send('persisted',{saved:true});controller.close();},120);
     }}),{headers:{'Content-Type':'text/event-stream'}});
    }
    return original(input,options);
   };
  },{fixture:installFixtures.toString()});
  const errors=[],escaped=[];await context.route('**/api/**',r=>{escaped.push(r.request().url());return r.fulfill({status:599,body:'{}'});});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);const input=page.getByRole('textbox',{name:'Message',exact:true});await input.fill('我想从轻松一点的活动开始');
  await page.evaluate(()=>{window.revealSamples=[];const log=document.querySelector('[role=log]');new MutationObserver(()=>{const rows=document.querySelectorAll('[data-streaming-reply]');if(rows.length>1)window.revealSamples.push(rows[rows.length-1].textContent);}).observe(log,{childList:true,subtree:true,characterData:true});});
  await input.press('Enter');
  await page.waitForFunction(()=>document.querySelectorAll('.reply-character').length>3);
  const mid=await page.evaluate(()=>({text:document.querySelectorAll('[data-streaming-reply]')[1].textContent,animation:getComputedStyle(document.querySelector('.reply-character:last-child')).animationName}));
  assert.equal(mid.animation,'reply-character-reveal');assert.ok(mid.text.length<44);
  await page.waitForFunction(()=>{const el=document.querySelectorAll('[data-streaming-reply]')[1];return el?.textContent==='可以先从轻松的一小步开始，留意身体和心情的变化，选择自己觉得舒服的节奏。🌿'&&el.getAttribute('aria-busy')==='false';});
  const samples=await page.evaluate(()=>[...new Set(window.revealSamples)]);assert.ok(samples.length>20);
  assert.equal(await page.locator('[data-streaming-reply]').first().locator('.reply-character').count(),0);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);
  results.push({width,progressiveSamples:samples.length,animation:mid.animation,historyInstant:true,completed:true});await context.close();
 }
 await fs.mkdir('.test-tmp/streaming-1014/deploy',{recursive:true});await fs.writeFile('.test-tmp/streaming-1014/deploy/browser-results.json',JSON.stringify({base,results},null,2));console.log(JSON.stringify({base,results}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
