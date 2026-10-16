// Real UI, synthetic backend. All network API fallthrough is forbidden.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {installFixtures}=require('./goal-formulation-1003.cjs');
const OUT=path.resolve(__dirname,'../../.test-tmp/bound-confirmation/browser');
exports.run=async(browser,base)=>{
 await fs.mkdir(OUT,{recursive:true});const results=[];
 for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:900},serviceWorkers:'block'});
  await context.addInitScript(installFixtures);
  await context.addInitScript(()=>{
   const f=window.goalCardFixture; const previous=window.fetch;
   f.card={id:'bound-secondary',kind:'secondary',phase:'ready',revision:6,content_revision:2,fields:{activity_content:'家务',schedule_text:'后天',duration_minutes:30},concerns:[]};
   f.allowed=true;f.failure=false;f.rejectHttp=false;f.sent=[];
   window.fetch=async(input,options={})=>{
    const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
    if(p==='/api/program/goal-card-ui/goal-card' && (!options.method||options.method==='GET')){
     return Response.json({enabled:true,card:{...f.card,confirmation:{allowed:f.allowed,code:f.allowed?null:'goal_card_unresolved_change',message:f.allowed?null:'你在展示卡片后提出了修改，请先重新核对。',card_id:f.card.id,revision:f.card.revision,display_message_id:66}}});
    }
    if(p==='/api/chat/stream'&&JSON.parse(options.body).metadata?.goal_card_action==='confirm'){
     const body=JSON.parse(options.body);f.sent.push(body);
     if(f.rejectHttp)return Response.json({detail:'目标卡展示已更新，请重新查看后确认。'},{status:409});
     const receipt={card_id:f.card.id,revision:f.card.revision,user_message_id:100+f.sent.length,saved:!f.failure,card:f.failure?null:{...f.card,phase:'confirmed',revision:7},message:f.failure?'保存暂时失败，卡片仍保留，请稍后重试。':'目标已确认并保存到“我的目标”。'};
     // Hold the natural-language reply: receipt must be visible immediately.
     return new Response(new ReadableStream({start(c){
      const frame=(e,x)=>c.enqueue(new TextEncoder().encode(`event: ${e}\ndata: ${JSON.stringify(x)}\n\n`));
      frame('meta',{session_id:'goal-card-ui',user_message_id:receipt.user_message_id,reply_module:'module_2',goal_card_confirmation:receipt});
      f.finish=()=>{if(receipt.saved)f.card={...f.card,phase:'confirmed',revision:7};frame('delta',{text:'请查看卡片状态。'});frame('persisted',{saved:true});c.close();};
     }}),{headers:{'Content-Type':'text/event-stream'}});
    }
    if(p==='/api/assessment/activity-candidates')return Response.json({items:[],has_more:false});
    return previous(input,options);
   };
  });
  const escaped=[],errors=[];
  await context.route('**/api/**',r=>{escaped.push(r.request().url());return r.fulfill({status:599,body:'{}'});});
  const page=await context.newPage();page.setDefaultTimeout(40000);page.on('pageerror',e=>errors.push(e.message));
  try{
   await page.goto(base,{waitUntil:'domcontentloaded',timeout:60000});const panel=page.getByRole('region',{name:'目标卡快捷入口'});
   // section may not be mapped to region by older engines; aria selector is canonical.
   const card=page.locator('[aria-label="目标卡快捷入口"]');const confirm=card.getByRole('button',{name:'确认这个安排',exact:true});
   await confirm.waitFor();await page.waitForFunction(()=>!document.querySelector('[aria-label="目标卡快捷入口"] button')?.disabled);
   assert(await confirm.isEnabled());
   await page.evaluate(()=>{window.goalCardFixture.allowed=false;window.goalCardFixture.broadcast();});
   await page.waitForFunction(()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='确认这个安排')?.disabled);
   assert((await card.textContent()).includes('提出了修改'));assert.equal(await page.evaluate(()=>window.goalCardFixture.sent.length),0);
   await page.evaluate(()=>{window.goalCardFixture.allowed=true;window.goalCardFixture.broadcast();});
   await page.waitForFunction(()=>![...document.querySelectorAll('button')].find(b=>b.textContent==='确认这个安排')?.disabled);
   // Backend rejects a race after the displayed readiness: exact reason reaches panel.
   await page.evaluate(()=>window.goalCardFixture.rejectHttp=true);await confirm.click();
   await card.getByRole('alert').filter({hasText:'展示已更新'}).waitFor();
   await page.evaluate(()=>{window.goalCardFixture.rejectHttp=false;window.goalCardFixture.failure=true;window.goalCardFixture.broadcast();});
   await page.waitForFunction(()=>![...document.querySelectorAll('button')].find(b=>b.textContent==='确认这个安排')?.disabled);
   await confirm.click();await card.getByRole('alert').filter({hasText:'保存暂时失败'}).waitFor();
   assert.equal(await page.evaluate(()=>window.goalCardFixture.card.phase),'ready');
   await page.screenshot({path:path.join(OUT,`failure-receipt-${width}.png`)});
   await page.evaluate(()=>window.goalCardFixture.finish());
   await page.waitForFunction(()=>![...document.querySelectorAll('button')].find(b=>b.textContent==='确认这个安排')?.disabled);
   await page.evaluate(()=>window.goalCardFixture.failure=false);await confirm.click();
   await card.waitFor({state:'detached'});
   assert.equal(await confirm.count(),0,'commit receipt must remove waiting button before the chat reply');
   await page.getByRole('button',{name:'查看 PA 目标卡',exact:true}).waitFor();
   const sent=await page.evaluate(()=>window.goalCardFixture.sent.at(-1));
   assert.deepEqual(sent.metadata,{goal_card_action:'confirm',goal_card_id:'bound-secondary',goal_card_revision:'6',goal_card_display_message_id:'66'});
   assert.equal(sent.message,'确认这张次要目标卡，就按这个安排。');
   await page.screenshot({path:path.join(OUT,`success-before-reply-${width}.png`)});
   await page.evaluate(()=>window.goalCardFixture.finish());
   await card.waitFor({state:'detached'});
   assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);
   results.push({width,serverReadiness:true,boundMetadata:true,preflightReason:true,failureReceipt:true,successBeforeReply:true});
  }catch(e){await page.screenshot({path:path.join(OUT,`error-${width}.png`)});throw e;}finally{await context.close();}
 }
 await fs.writeFile(path.join(OUT,'results.json'),JSON.stringify(results,null,2));return results;
};
if(require.main===module)(async()=>{const {chromium}=require('playwright');const browser=await chromium.launch({headless:true,channel:'chrome'});try{console.log(JSON.stringify(await exports.run(browser,process.argv[2]||'http://127.0.0.1:3163'),null,2));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
