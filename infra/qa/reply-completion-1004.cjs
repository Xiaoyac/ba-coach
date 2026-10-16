// Real bundle, synthetic API only; verify terminal events independently of slow history reads.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');const path=require('node:path');
const {installFixtures}=require('./goal-formulation-1003.cjs');
const OUT=path.resolve(__dirname,'../../.test-tmp/reply-completion');
function completionFixture(){
 const f=window.goalCardFixture;f.streams=[];f.heldReads=[];f.holdReads=false;
 const original=window.fetch.bind(window);
 window.fetch=async(input,options={})=>{
  const url=new URL(typeof input==='string'?input:input.url,location.href);
  if(url.pathname==='/api/conversations/goal-card-ui' && f.holdReads){
   const response=await original(input,options);
   return new Promise((resolve,reject)=>{f.heldReads.push(()=>resolve(response));options.signal?.addEventListener('abort',()=>reject(options.signal.reason),{once:true});});
  }
  if(url.pathname==='/api/chat/stream'){
   const body=JSON.parse(options.body),userId=11+f.streams.length*2;
   f.messages.push({id:userId,role:'user',content:body.message});
   return new Response(new ReadableStream({start(c){
    const emit=(event,data)=>c.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
    const stream={emit,c,userId};f.streams.push(stream);
    emit('meta',{session_id:'goal-card-ui',user_message_id:userId});
    options.signal?.addEventListener('abort',()=>{try{c.error(options.signal.reason);}catch{}},{once:true});
   }}),{headers:{'Content-Type':'text/event-stream'}});
  }
  return original(input,options);
 };
}
exports.run=async(browser,base,baseline=false)=>{
 await fs.mkdir(OUT,{recursive:true});const result=[];
 for(const width of [1440,390,360]){
  const context=await browser.newContext({viewport:{width,height:900}});
  await context.addInitScript(installFixtures,{settings:{routing_mode:'router_only',reply_mode:'ack_deep',reply_effort:'low',knowledge_mediator_enabled:true}});
  await context.addInitScript(completionFixture);
  const page=await context.newPage(),errors=[],escaped=[];
  page.on('pageerror',e=>errors.push(e.message));await page.route('**/api/**',r=>{escaped.push(r.request().url());return r.fulfill({status:599,body:'Forbidden real API'});});
  try{
   await page.goto(base);const input=page.getByRole('textbox',{name:'Message'});await input.fill('合成测试：从小活动开始');await input.press('Enter');
   await page.waitForFunction(()=>window.goalCardFixture.streams.length===1);
   await page.evaluate(()=>{const s=window.goalCardFixture.streams[0];s.emit('delta',{text:'这是完整的合成正文。',phase:'body'});s.emit('done',{});});
   await page.getByText('这是完整的合成正文。',{exact:true}).waitFor();
   await page.waitForTimeout(200);
   if(baseline)assert.equal(await page.locator('[data-generation-status]').count(),1,'baseline continues claiming generation after done');
   else{assert.equal(await page.locator('[data-generation-status]').count(),0);await page.getByText('回复已生成，正在确认保存结果…',{exact:true}).waitFor();await page.screenshot({path:path.join(OUT,`saving-${width}.png`)});}
   await page.evaluate(()=>{const f=window.goalCardFixture;f.holdReads=true;f.streams[0].emit('persisted',{saved:true});});
   await page.waitForFunction(()=>window.goalCardFixture.heldReads.length>0);await page.waitForTimeout(250);
   if(baseline){assert.equal(await page.locator('[data-generation-status]').count(),1);}
   else{
    assert.equal(await page.locator('[data-generation-status]').count(),0);
    await input.fill('第二次独立输入');const send=page.getByRole('button',{name:'Send',exact:true});assert.equal(await send.isEnabled(),true,'durable receipt releases composer before slow GET');
    await send.click();await page.waitForFunction(()=>window.goalCardFixture.streams.length===2);
    await page.evaluate(()=>{const f=window.goalCardFixture;f.holdReads=false;for(const release of f.heldReads.splice(0))release();});
    await page.waitForTimeout(250);assert.equal(await page.locator('[data-generation-status]').count(),1,'old GET cannot clear new generation');
    await page.evaluate(()=>{const s=window.goalCardFixture.streams[1];s.emit('delta',{text:'第二轮正文。'});s.emit('done',{});s.emit('persisted',{saved:false});});
    await page.getByText('回复已生成，但保存失败，请先复制回复后再刷新。',{exact:true}).waitFor();
    assert.equal(await page.locator('[data-generation-status]').count(),0);
    await input.fill('第三轮停止测试');await input.press('Enter');
    await page.waitForFunction(()=>window.goalCardFixture.streams.length===3);
    await page.evaluate(()=>{const f=window.goalCardFixture;f.holdReads=true;f.streams[2].emit('cancelled',{cancelled:true});});
    await page.getByText('已停止生成，未完成的回复未保存。你可以继续发送消息。',{exact:true}).waitFor();
    assert.equal(await page.locator('[data-generation-status]').count(),0);
    assert.equal(await page.getByText('回复已生成，正在确认保存结果…',{exact:true}).count(),0);
   }
   assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);
   await page.screenshot({path:path.join(OUT,`${baseline?'baseline':'fixed'}-${width}.png`)});
   result.push({width,baseline,passed:true,checks:['done_vs_persistence','slow_final_get',...baseline?[]:['new_turn_race','save_failure','cancelled_receipt']]});
  }finally{await context.close();}
 }
 return result;
};
if(require.main===module)(async()=>{const {chromium}=require('playwright');const browser=await chromium.launch({channel:'chrome',headless:true});try{console.log(JSON.stringify(await exports.run(browser,process.argv[2],process.argv.includes('--baseline')),null,2));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
