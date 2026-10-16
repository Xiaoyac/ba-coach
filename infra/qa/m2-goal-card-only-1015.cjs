const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {installFixtures}=require('./goal-formulation-1003.cjs');
const base=process.argv[2]||'http://127.0.0.1:3144';const out=path.resolve(__dirname,'../../.test-tmp/m2-card-only-1015');
(async()=>{const browser=await chromium.launch({channel:'chrome',headless:true});const results=[];try{
 for(const [width,role,style] of [[1440,'admin','default'],[390,'user','default'],[360,'user','default'],[390,'admin','participant-1']]){
 const context=await browser.newContext({viewport:{width,height:900},serviceWorkers:'block'});
 await context.addInitScript(installFixtures,{role});
 await context.addInitScript(style=>{
  localStorage.setItem('bacoach-overall-style:goal-test',style);
  const f=window.goalCardFixture;f.module='module_1';f.card={id:'draft-one',kind:'primary',phase:'ready',revision:4,content_revision:1,fields:{activity_content:'午后散步十分钟',duration_minutes:10},concerns:[]};f.cardReads=0;
  const previous=window.fetch;window.fetch=async(input,options={})=>{
   const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
   if(p==='/api/program/goal-card-ui/goal-card'&&(!options.method||options.method==='GET')){f.cardReads++;if(f.readFails)return Response.json({detail:'读取失败，稍后重试'},{status:503});return Response.json({enabled:true,card:f.card?{...f.card,confirmation:{allowed:true,card_id:f.card.id,revision:f.card.revision,display_message_id:66}}:null})}
   if(p==='/api/program/goals/overview')return Response.json({enabled:true,goals:[],pa_cards:[],formulation_cards:[]});
   if(p.includes('/execution'))return Response.json({session_id:'goal-card-ui',stages:[],turns:[],state:'empty'});
   if(p==='/api/assessment/activity-candidates')return Response.json({items:[]});
   return previous(input,options);
  };
 },style);
 const page=await context.newPage(),errors=[],escaped=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(40000);
 await context.route('**/api/**',r=>{escaped.push(r.request().url());return r.fulfill({status:599,body:'{}'})});
 const inline=page.locator('[aria-label="目标卡快捷入口"]');const shortcut=page.getByRole('button',{name:'查看 PA 目标卡',exact:true});const viewer=page.getByRole('dialog',{name:'PA 目标卡',exact:true});
 const refresh=async data=>{await page.evaluate(d=>{const f=window.goalCardFixture;Object.assign(f,d);f.broadcast()},data);await page.waitForTimeout(120)};
 try{
  await page.goto(base,{waitUntil:'domcontentloaded',timeout:60000});await shortcut.waitFor();await page.waitForFunction(()=>window.goalCardFixture.cardReads>0);assert.equal(await inline.count(),0);
  await shortcut.click();await viewer.waitFor();await viewer.getByText('午后散步十分钟',{exact:true}).waitFor();assert.equal(await viewer.locator('input,textarea').count(),0);await page.getByRole('button',{name:'关闭 PA 目标卡',exact:true}).click();
  // M2 needs a real active planning card, not an empty placeholder or archive.
  await refresh({module:'module_2',card:null});await page.waitForFunction(()=>window.goalCardFixture.cardReads>1);await inline.waitFor({state:'detached'});
  for(const phase of ['formulating','discussing','ready']){
   await refresh({card:{id:'draft-one',kind:'primary',phase,revision:5,fields:{activity_content:'午后散步十分钟',duration_minutes:10},concerns:[]}});await inline.waitFor();
  }
  const open=inline.getByRole('button',{name:'先查看或修改',exact:true});await open.click();const activity=inline.getByRole('textbox',{name:'想做的活动',exact:true});
  await activity.fill('尚未提交的散步修改');
  await refresh({module:'module_3'});await inline.waitFor({state:'detached'});await shortcut.click();await viewer.getByText('尚未提交的散步修改',{exact:true}).waitFor();assert.equal(await viewer.locator('input,textarea').count(),0);await page.getByRole('button',{name:'关闭 PA 目标卡',exact:true}).click();
  await refresh({module:'module_2'});await inline.waitFor();await inline.getByRole('button',{name:'先查看或修改',exact:true}).click();assert.equal(await activity.inputValue(),'尚未提交的散步修改');
  // Once confirmed, even the unsent local edit cannot reopen the inline card.
  await page.evaluate(()=>{const f=window.goalCardFixture;f.card={...f.card,phase:'confirmed',revision:6};f.broadcast()});await inline.waitFor({state:'detached'});await shortcut.click();await viewer.getByText('尚未提交的散步修改',{exact:true}).waitFor();
  // Moving to a different module closes an open viewer, without clearing draft.
  await refresh({module:'module_4'});await viewer.waitFor({state:'detached'});await inline.waitFor({state:'detached'});
  await page.screenshot({path:path.join(out,`icon-only-${width}-${role}-${style}.png`)});
  for(const module of ['module_1','module_3','module_4'])for(const phase of ['formulating','discussing','ready','paused','confirmed']){
   await refresh({module,card:{id:'state-'+phase,kind:'primary',phase,revision:1,fields:{activity_content:'午后散步十分钟'},concerns:[]}});assert.equal(await inline.count(),0,module+'/'+phase);
  }
  await refresh({readFails:true});await shortcut.click();await viewer.getByRole('alert').filter({hasText:'读取失败'}).waitFor();assert.equal(await inline.count(),0);await page.screenshot({path:path.join(out,`viewer-${width}-${role}-${style}.png`)});
  await page.evaluate(()=>window.goalCardFixture.readFails=false);await viewer.getByRole('button',{name:'重试读取',exact:true}).click();await viewer.getByRole('alert').waitFor({state:'detached'});
  await viewer.getByRole('button',{name:'查看目标与记录',exact:true}).click();await viewer.waitFor({state:'detached'});await page.getByRole('dialog',{name:style==='participant-1'?'活动计划与记录':'目标与记录',exact:true}).waitFor();await page.getByRole('button',{name:'关闭目标与记录',exact:true}).click();
  await refresh({module:'module_2',card:{id:'paused',kind:'primary',phase:'paused',revision:1,fields:{activity_content:'暂存计划'},concerns:[]}});await inline.waitFor({state:'detached'});await shortcut.click();await viewer.getByRole('button',{name:'继续完善草稿',exact:true}).waitFor();await page.getByRole('button',{name:'关闭 PA 目标卡',exact:true}).click();
  await page.reload({waitUntil:'domcontentloaded',timeout:60000});await shortcut.waitFor();await page.waitForFunction(()=>window.goalCardFixture.cardReads>0);await inline.waitFor({state:'detached'});await shortcut.click();await viewer.getByText('尚未提交的散步修改',{exact:true}).waitFor();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);
  results.push({width,role,style,onlyM2ActiveCard:true,otherModulesAllPhasesHidden:true,dirtyRetained:true,readErrorHidden:true,viewerAndArchive:true,pausedHidden:true,reloadDraftRetained:true});
 }catch(e){console.error(JSON.stringify({errors,escaped,body:(await page.locator('body').innerText()).slice(0,1800)}));await page.screenshot({path:path.join(out,`failure-${width}-${role}.png`)});throw e}finally{await context.close()}
 }
 await fs.writeFile(path.join(out,'ui-results.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
