// Current flow, separate history navigation, and browser-measured first body timing.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {installFixtures}=require('./goal-formulation-1003.cjs');
const base=process.argv[2]||'http://127.0.0.1:3186',out='.test-tmp/execution-history-1015';
(async()=>{await fs.mkdir(out,{recursive:true});const browser=await chromium.launch({channel:'chrome',headless:true});const results=[];
try{for(const [width,theme] of [[1440,'porcelain'],[390,'github'],[390,'warm'],[1440,'dark']]){
const context=await browser.newContext({viewport:{width,height:900},serviceWorkers:'block'});
await context.addInitScript(({fixture,theme})=>{
 (0,eval)(`(${fixture})`)({role:'admin'});window.goalCardFixture.module='module_1';localStorage.setItem('psy-theme',theme);
 localStorage.setItem('bacoach-overall-style:goal-test','default');
 const f=window.drawerFixture={timings:JSON.parse(sessionStorage.getItem('drawer-times')||'{"19":4321}'),writes:[],bodyArrivedAt:0,sentAt:0,busy:false,currentId:Number(sessionStorage.getItem('drawer-current')||21),empty:false,failHistory:false,slowDetail:false,detailCalls:[]};
 const turns=[{id:103,preview:'更新后的当前轮',created_at:'2026-10-15T09:00:00Z'},{id:101,preview:'我想从轻松一点的活动开始',created_at:'2026-10-15T08:00:00Z'},{id:21,preview:'以前的一轮，没有客户端计时',created_at:'2026-10-14T08:00:00Z'},{id:19,preview:'昨天散步后心情放松了一点',created_at:'2026-10-13T08:00:00Z'},{id:17,preview:'较早的运行记录',created_at:'2026-10-12T08:00:00Z'},{id:15,preview:'加载到更早的记录',created_at:'2026-10-11T08:00:00Z'}];
 const original=window.fetch;window.fetch=async(input,options={})=>{
  const p=new URL(typeof input==='string'?input:input.url,location.href),g=window.goalCardFixture;
  if(p.pathname.endsWith('/execution/client-timing')){const v=JSON.parse(options.body);f.timings[v.user_message_id]=v.first_body_ms;f.writes.push(v);sessionStorage.setItem('drawer-times',JSON.stringify(f.timings));return Response.json({first_body_ms:v.first_body_ms});}
  if(p.pathname.endsWith('/execution/history')){
   if(f.failHistory)return Response.json({detail:'历史记录暂时不可用'},{status:503});
   const before=Number(p.searchParams.get('before_id')||f.currentId),all=f.empty?[]:turns.filter(t=>t.id<f.currentId&&t.id<before),page=all.slice(0,2);
   return Response.json({session_id:'goal-card-ui',current_user_message_id:f.currentId,turns:page,next_before_id:all.length>2?page.at(-1).id:null});
  }
  if(p.pathname.endsWith('/execution')){
   const selected=Number(p.searchParams.get('user_message_id')||f.currentId),history=p.searchParams.has('user_message_id');
   if(history)f.detailCalls.push(selected);
   if(selected===17&&f.slowDetail)await new Promise(resolve=>setTimeout(resolve,400));
   return Response.json({session_id:'goal-card-ui',user_message_id:selected,module:'module_1',live:f.busy,request_finished:!f.busy,state:f.busy?'running':'completed',client_first_body_ms:f.timings[selected]??null,
    turns:turns.filter(t=>t.id<=f.currentId),stages:[{stage:'risk_gate',label:'风险判断',lane:'foreground',status:'completed',model:'deepseek-v4.1-flash',duration_ms:320,calls:1},{stage:'main_generation',label:'正文生成',lane:'foreground',status:selected===17?'running':'completed',model:(selected===f.currentId?'current-':'history-')+selected,duration_ms:1250,elapsed_ms:900,calls:1}],server_time:Date.now()/1000});
  }
  if(p.pathname==='/api/chat/stream'){
   const body=JSON.parse(options.body);f.sentAt=performance.now();f.busy=true;
   const lead='我听到了，我们可以慢慢聊一聊。',text='先从你觉得轻松的一步开始，选择适合自己的节奏。';
   return new Response(new ReadableStream({start(c){const send=(e,d)=>c.enqueue(new TextEncoder().encode(`event: ${e}\ndata: ${JSON.stringify(d)}\n\n`));
    send('meta',{session_id:'goal-card-ui',user_message_id:101,reply_module:'module_1',next_module:'module_1'});
    setTimeout(()=>send('delta',{text:lead,phase:'lead'}),100);
    setTimeout(()=>{f.currentId=101;},300);
    setTimeout(()=>{f.bodyArrivedAt=performance.now();send('delta',{text:'\n\n'+text,phase:'body'});
      g.messages.push({id:101,role:'user',content:body.message},{id:102,role:'assistant',reply_to_message_id:101,content:lead+'\n\n'+text});g.revision++;f.busy=false;
      send('done',{});send('persisted',{saved:true});c.close();},1000);
   }}),{headers:{'Content-Type':'text/event-stream'}});
  }
  return original(input,options);
 };
},{fixture:installFixtures.toString(),theme});
const escaped=[],errors=[];await context.route('**/api/**',r=>{escaped.push(r.request().url());return r.fulfill({status:599,body:'{}'});});
const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(15000);
await page.goto(base);const input=page.getByRole('textbox',{name:'Message',exact:true});await input.fill('我想从轻松一点的活动开始');
const trigger=page.getByRole('button',{name:'模型运行状态',exact:true}),current=page.getByRole('dialog',{name:'模型运行状态',exact:true}),history=page.getByRole('dialog',{name:'历史运行记录',exact:true});
const openHistory=()=>current.getByRole('button',{name:'历史运行记录',exact:true}).click();
await trigger.click();await current.getByText('current-21',{exact:true}).waitFor();
assert.equal(await current.locator('select,[role="combobox"]').count(),0);assert.equal(await current.getByText('跟随最新一轮',{exact:true}).count(),0);
assert.equal(await current.getByText('昨天散步后心情放松了一点',{exact:true}).count(),0);
await page.screenshot({path:`${out}/current-${theme}-${width}.png`});
await openHistory();const record19=history.getByRole('button',{name:'查看运行记录：昨天散步后心情放松了一点',exact:true});await record19.waitFor();
assert.equal(await history.getByRole('button',{name:'查看运行记录：以前的一轮，没有客户端计时',exact:true}).count(),0,'current run is excluded');
await history.getByRole('button',{name:'加载更早的记录',exact:true}).click();await history.getByRole('button',{name:'查看运行记录：加载到更早的记录',exact:true}).waitFor();
await page.screenshot({path:`${out}/history-${theme}-${width}.png`});
await record19.click();await history.locator('[data-first-body-timing]').filter({hasText:'4.32'}).waitFor();
await page.keyboard.press('Escape');await record19.waitFor();assert.equal(await record19.evaluate(e=>e===document.activeElement),true);
await history.getByRole('button',{name:'查看运行记录：较早的运行记录',exact:true}).click();await history.getByText('history-17',{exact:true}).waitFor();
assert.equal(await history.locator('[data-motion]').count(),0);assert.equal(await history.locator('[data-status="running"] span').first().evaluate(e=>getComputedStyle(e).animationName),'none');
await page.keyboard.press('Escape');await page.evaluate(()=>{window.drawerFixture.slowDetail=true;});await history.getByRole('button',{name:'查看运行记录：较早的运行记录',exact:true}).click();await history.getByRole('button',{name:'返回历史列表',exact:true}).click();await record19.click();await history.getByText('history-19',{exact:true}).waitFor();await page.waitForTimeout(500);assert.equal(await history.getByText('history-17',{exact:true}).count(),0,'late history response cannot replace another record');await page.keyboard.press('Escape');await page.keyboard.press('Escape');await current.getByText('current-21',{exact:true}).waitFor();
await page.getByRole('button',{name:'关闭运行状态'}).click();await input.press('Enter');await trigger.click();
await page.waitForTimeout(200);assert.equal(await current.getByText('current-21',{exact:true}).count(),0,'new send hides old flow before next server poll');
assert.equal(await page.evaluate(()=>window.drawerFixture.writes.length),0,'lead must not count as body');
await page.waitForFunction(()=>window.drawerFixture.writes.length===1);
const metrics=await page.evaluate(()=>({value:window.drawerFixture.writes[0].first_body_ms,network:window.drawerFixture.bodyArrivedAt-window.drawerFixture.sentAt}));
assert.ok(metrics.value>=metrics.network+10);assert.ok(metrics.value<3000);
await current.locator('[data-first-body-timing]').filter({hasText:(metrics.value/1000).toFixed(2)}).waitFor().catch(async error=>{await page.screenshot({path:out+'/failure.png'});console.error({width,theme,metrics,body:await page.locator('body').innerText(),fixture:await page.evaluate(()=>window.drawerFixture),errors});throw error;});
await openHistory();await history.getByRole('button',{name:'查看运行记录：以前的一轮，没有客户端计时',exact:true}).click();
await history.getByText('本轮未记录客户端耗时',{exact:true}).waitFor();assert.equal((await history.locator('[data-first-body-timing]').textContent()).trim(),'—');
await page.keyboard.press('Escape');await record19.click();await history.getByText('history-19',{exact:true}).waitFor();
await page.evaluate(()=>{window.drawerFixture.currentId=103;window.drawerFixture.timings[103]=987;sessionStorage.setItem('drawer-current','103');});
await page.waitForTimeout(1800);assert.ok(await history.getByText('history-19',{exact:true}).isVisible(),'current polls never replace selected history');
await page.keyboard.press('Escape');await history.getByRole('button',{name:'返回当前流程',exact:true}).click();await current.getByText('current-103',{exact:true}).waitFor();
await current.locator('[data-first-body-timing]').filter({hasText:'0.99'}).waitFor();
await page.evaluate(()=>{window.drawerFixture.failHistory=true;});await openHistory();await history.getByRole('alert').waitFor();await page.evaluate(()=>{window.drawerFixture.failHistory=false;});await history.getByRole('button',{name:'重试',exact:true}).click();await history.getByRole('button',{name:'加载更早的记录',exact:true}).click();await record19.waitFor();
await page.keyboard.press('Escape');await page.evaluate(()=>{window.drawerFixture.empty=true;});await openHistory();await history.getByText('暂无历史运行记录',{exact:true}).waitFor();
await page.keyboard.press('Escape');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
await page.reload();await trigger.click();await openHistory();await history.getByRole('button',{name:'查看运行记录：我想从轻松一点的活动开始',exact:true}).click();await history.locator('[data-first-body-timing]').filter({hasText:(metrics.value/1000).toFixed(2)}).waitFor();
assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);results.push({width,theme,...metrics,historySeparated:true,pagination:true,emptyAndRetry:true,staticHistory:true,currentIsolation:true,reload:true,keyboard:true});await context.close();
}console.log(JSON.stringify(results));await fs.writeFile(`${out}/results.json`,JSON.stringify(results,null,2));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
