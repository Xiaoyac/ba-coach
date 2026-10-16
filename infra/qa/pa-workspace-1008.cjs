// Real React UI, isolated synthetic APIs. No participant data or remote writes.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {installFixtures}=require('./goal-formulation-1003.cjs');
const OUT=path.resolve(__dirname,'../../.test-tmp/pa-workspace-1008/browser');
exports.run=async(browser,base)=>{
 await fs.mkdir(OUT,{recursive:true});const results=[];
 for(const width of [1440,390,360]){
  const context=await browser.newContext({viewport:{width,height:width>600?900:844},serviceWorkers:'block',reducedMotion:'reduce'});
  await context.addInitScript(installFixtures);
  await context.addInitScript(()=>{const original=window.fetch;window.fetch=(input,options)=>{const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;if(p==='/api/assessment/activity-candidates')return Promise.resolve(Response.json({items:[],has_more:false}));return original(input,options);};});
  const escaped=[],errors=[];
  await context.route('**/api/**',route=>{escaped.push(route.request().url());return route.fulfill({status:599,body:'{}'});});
  const page=await context.newPage();page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message));
  try{
   await page.goto(base);const panel=page.getByRole('region',{name:'目标卡快捷入口',exact:true});
   await panel.waitFor();await page.waitForFunction(()=>window.goalCardFixture.calls.some(c=>c.path.endsWith('/events')));
   const header=page.locator('#chat-header-tools');assert.equal(await header.isVisible(),false);
   const menu=page.getByRole('button',{name:/专心聊，目标、记录和档案都在这里/});await menu.click();assert(await header.isVisible());
   await menu.click();assert.equal(await header.isVisible(),false);
   await page.evaluate(()=>{window.goalCardFixture.module='module_1';window.goalCardFixture.broadcast();});await header.waitFor({state:'visible'});
   await page.evaluate(()=>{window.goalCardFixture.module='module_2';window.goalCardFixture.setCard('primary','draft-a');});
   const activity=panel.getByLabel('想做的活动',{exact:true});await activity.waitFor();await activity.fill('暂存：明天在阳台站立伸展五分钟');
   await page.evaluate(()=>window.goalCardFixture.failPut=true);
   await panel.getByRole('button',{name:'暂存，继续聊天',exact:true}).click();await panel.getByRole('alert').waitFor();assert.equal(await activity.inputValue(),'暂存：明天在阳台站立伸展五分钟');
   assert.equal(await page.evaluate(()=>window.goalCardFixture.chats.length),0);
   await page.evaluate(()=>window.goalCardFixture.failPut=false);
   await panel.getByRole('button',{name:'暂存，继续聊天',exact:true}).click();await panel.getByText('草稿已暂存，教练可以读到；还未确认执行。',{exact:true}).waitFor();
   let state=await page.evaluate(()=>window.goalCardFixture);assert.equal(state.chats.length,0);assert.equal(state.writes.at(-1).mode,'draft');assert.equal(state.card.fields.activity_content,'暂存：明天在阳台站立伸展五分钟');
   await page.evaluate(()=>{const f=window.goalCardFixture;f.card={...f.card,phase:'ready',revision:12,content_revision:2};f.broadcast();});
   await panel.getByText(/教练已核对内容第 2 版/).waitFor();assert.equal(await panel.getByText(/第 12 版/).count(),0);
   await panel.getByRole('button',{name:'打开卡片',exact:true}).click();await activity.waitFor();
   await page.screenshot({path:path.join(OUT,`m2-ready-${width}.png`)});
   if(width<600) await page.setViewportSize({width,height:360});
   const composer=page.getByRole('textbox',{name:'Message',exact:true});await composer.focus();await composer.fill('我还想调整一下时间\n这样可以吗？');await activity.waitFor({state:'hidden'});
   const bounds=await composer.boundingBox();assert(bounds.y>=0&&bounds.y+bounds.height<=(width<600?360:900));
   const send=await page.getByRole('button',{name:'Send',exact:true}).boundingBox();assert(send.y+send.height<=(width<600?360:900));
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:path.join(OUT,`m2-typing-${width}.png`)});
   await composer.fill('');await composer.blur();await page.setViewportSize({width,height:width>600?900:844});
   await page.evaluate(()=>{const f=window.goalCardFixture;f.card={...f.card,phase:'confirmed'};f.module='module_3';f.broadcast();});
   await panel.getByRole('button',{name:'查看',exact:true}).waitFor();assert((await panel.boundingBox()).height<=44);
   const daily=page.getByRole('region',{name:'每日记录快捷入口',exact:true});await daily.getByText('今天暂无记录',{exact:true}).waitFor();
   assert((await daily.boundingBox()).height<=60);assert.equal(await daily.getByRole('button',{name:'历史与趋势',exact:true}).count(),0);
   await page.screenshot({path:path.join(OUT,`m3-${width}.png`)});
   await daily.getByRole('button',{name:'记录今天',exact:true}).click();const dialog=page.locator('dialog[open]');await dialog.waitFor();
   const details=dialog.locator('details').filter({has:page.locator('summary').filter({hasText:'补充活动明细'})});assert.equal(await details.getAttribute('open'),'');
   assert(await details.getByRole('textbox',{name:'活动 1 的内容',exact:true}).first().isVisible());
   await details.getByRole('textbox',{name:'活动 1 的内容',exact:true}).first().scrollIntoViewIfNeeded();
   await page.screenshot({path:path.join(OUT,`m3-record-${width}.png`)});
   assert.deepEqual(escaped,[]);assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>window.goalCardFixture.unknown),[]);
   results.push({width,draftSavedWithoutChat:true,failedSaveRetained:true,contentVersionStable:true,keyboardComposerVisible:true,m3Compact:true,activitiesExpanded:true});
  }catch(error){await page.screenshot({path:path.join(OUT,`failure-${width}.png`)});throw error;}finally{await context.close();}
 }
 await fs.writeFile(path.join(OUT,'results.json'),JSON.stringify(results,null,2));return results;
};
if(require.main===module)(async()=>{const {chromium}=require('playwright');const browser=await chromium.launch({headless:true,channel:'chrome'});try{console.log(JSON.stringify(await exports.run(browser,process.argv[2]||'http://127.0.0.1:3150'),null,2));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
