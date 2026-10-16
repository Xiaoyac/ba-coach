const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {installFixtures}=require('./goal-formulation-1003.cjs');
const out=path.resolve(__dirname,'../../.test-tmp/confirmed-goal-icon-1015');
(async()=>{const browser=await chromium.launch({headless:true,channel:'chrome'});const results=[];try{
 for(const [width,role,style] of [[1440,'admin','default'],[390,'user','default'],[360,'user','default'],[390,'admin','participant-1']]){
  const context=await browser.newContext({viewport:{width,height:900},serviceWorkers:'block'});
  await context.addInitScript(installFixtures,{role});
  await context.addInitScript(style=>{
   localStorage.setItem('bacoach-overall-style:goal-test',style);
   const f=window.goalCardFixture;f.card ||= {id:'primary-card',kind:'primary',phase:'ready',revision:6,content_revision:2,fields:{activity_content:'午后散步十分钟',schedule_text:'明天下午',duration_minutes:10},concerns:[]};
   const previous=window.fetch;window.fetch=async(input,options={})=>{
    const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
    if(p==='/api/program/goal-card-ui/goal-card'&&(!options.method||options.method==='GET'))return Response.json({enabled:true,card:{...f.card,confirmation:{allowed:true,card_id:f.card.id,revision:f.card.revision,display_message_id:66}}});
    if(p==='/api/program/goals/overview')return Response.json({enabled:true,goals:[],pa_cards:f.card.phase==='confirmed'?[{id:'goal-1',card_id:'cycle-1',title:'午后散步十分钟',goal_kind:'primary',status:'active',created_at:'2026-10-15T00:00:00Z',plan:{activity_content:'午后散步十分钟',schedule_text:'明天下午',duration_minutes:10}}]:[]});
    if(p==='/api/chat/stream'&&JSON.parse(options.body).metadata?.goal_card_action==='confirm'){
     if(f.reject)return Response.json({detail:'保存失败，请稍后重试'},{status:409});
     f.card={...f.card,phase:'confirmed',revision:7};sessionStorage.setItem('qa-goal-restore-card',JSON.stringify(f.card));
     const receipt={card_id:f.card.id,revision:6,user_message_id:21,saved:true,card:f.card,message:'已保存'};
     return new Response(new ReadableStream({start(c){const emit=(e,v)=>c.enqueue(new TextEncoder().encode(`event: ${e}\ndata: ${JSON.stringify(v)}\n\n`));emit('meta',{session_id:'goal-card-ui',user_message_id:21,reply_module:'module_2',goal_card_confirmation:receipt});f.finish=()=>{emit('delta',{text:'安排已经保存。'});emit('persisted',{saved:true});c.close();};}}),{headers:{'Content-Type':'text/event-stream'}});
    }
    if(p.includes('/execution'))return Response.json({session_id:'goal-card-ui',stages:[],turns:[],state:'empty'});
    return previous(input,options);
   };
  },style);
  const escaped=[],errors=[];await context.route('**/api/**',r=>{escaped.push(r.request().url());return r.fulfill({status:599,body:'{}'});});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(15000);
  try{
   await page.goto(process.argv[2]||'http://127.0.0.1:3142',{waitUntil:'domcontentloaded',timeout:60000});
   const card=page.locator('[aria-label="目标卡快捷入口"]'),confirm=card.getByRole('button',{name:'确认这个安排',exact:true});
   await confirm.waitFor();await page.evaluate(()=>window.goalCardFixture.reject=true);await confirm.click();await card.getByRole('alert').filter({hasText:'保存失败'}).waitFor();assert.ok(await card.isVisible());
   await page.evaluate(()=>window.goalCardFixture.reject=false);await confirm.click();await card.waitFor({state:'detached'});
   const shortcut=page.getByRole('button',{name:'查看 PA 目标卡',exact:true});assert.equal(await shortcut.count(),1);await shortcut.waitFor();
   const b=await shortcut.boundingBox();assert.ok(b&&b.y<180&&b.x>width/2);
   await page.screenshot({path:path.join(out,`collapsed-${width}-${style}.png`)});
   await shortcut.click();await page.getByRole('dialog',{name:'PA 目标卡',exact:true}).getByRole('button',{name:'查看目标与记录',exact:true}).click();const dialog=page.getByRole('dialog',{name:style==='participant-1'?'活动计划与记录':'目标与记录',exact:true});await dialog.waitFor();await dialog.getByText('午后散步十分钟',{exact:true}).first().waitFor();
   await page.screenshot({path:path.join(out,`opened-${width}-${style}.png`)});await page.getByRole('button',{name:'关闭目标与记录',exact:true}).click();await page.evaluate(()=>window.goalCardFixture.finish());
   for(const module of ['module_3','module_4','module_1']){await page.evaluate(m=>{window.goalCardFixture.module=m;window.goalCardFixture.broadcast()},module);await shortcut.waitFor();await card.waitFor({state:'detached'});}
   await page.reload({waitUntil:'domcontentloaded',timeout:60000});await shortcut.waitFor();await shortcut.click();await page.getByRole('dialog',{name:'PA 目标卡',exact:true}).getByText(/已保存/).waitFor();await page.getByRole('button',{name:'关闭 PA 目标卡',exact:true}).click();await card.waitFor({state:'detached'});
   await page.evaluate(()=>window.goalCardFixture.setCard('primary','next-card'));await card.waitFor();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);
   results.push({width,role,style,confirmedHidden:true,failedStillVisible:true,archiveOpens:true,modulesAndReload:true,newDraftVisible:true});
  }catch(e){await page.screenshot({path:path.join(out,`failure-${width}.png`)});throw e;}finally{await context.close()}
 }
 console.log(JSON.stringify(results));await fs.writeFile(path.join(out,'ui-results.json'),JSON.stringify(results,null,2));
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
