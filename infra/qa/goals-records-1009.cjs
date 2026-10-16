// All API traffic is synthetic and blocked at the network if not mocked.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { installFixtures } = require('./daily-experience-1002.cjs');
const OUT = path.resolve(__dirname, '../../.test-tmp/requirements-1009/browser');
const SUMMARY = ['想做的事情完成程度', '这一天总体身体活动程度', '这一天整体心情如何？'];
function installGoals() {
  const original = window.fetch.bind(window);
  const goal={id:'fixture-goal',title:'饭后散步十分钟',status:'active',goal_kind:'primary',created_at:'2026-10-01T10:00:00Z',updated_at:'2026-10-02T10:00:00Z',plan:{activity_content:'饭后散步十分钟',schedule_text:'每天晚饭后',location:'小区',duration_minutes:10}};
  window.fetch=async(input,options={})=>{
    const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
    if(p==='/api/program/goals/overview')return new Response(JSON.stringify({enabled:true,goals:[goal]}),{headers:{'Content-Type':'application/json'}});
    if(p==='/api/program/goals/fixture-goal/history')return new Response(JSON.stringify({goal,plans:[{...goal.plan,id:'plan1',version_no:1,record_status:'confirmed',created_at:goal.created_at,updated_at:goal.updated_at}],cycles:[],activities:[],totals:{plans:1,cycles:0,activities:0},page:1,page_size:12}),{headers:{'Content-Type':'application/json'}});
    return original(input,options);
  };
}
async function noOverflow(page) {
  const result = await page.evaluate(() => [...document.querySelectorAll('dialog[open], [role="tabpanel"]:not([hidden]), [data-daily-scroll]')].filter(e => e.getClientRects().length).map(e => {
    const r = e.getBoundingClientRect(); return { width:e.clientWidth, scroll:e.scrollWidth, left:r.left, right:r.right, bottom:r.bottom };
  }));
  for (const r of result) { assert.ok(r.left >= -1 && r.right <= (await page.viewportSize()).width + 1, JSON.stringify(r)); assert.ok(r.scroll <= r.width + 1, JSON.stringify(r)); }
}
exports.run = async function run(browser, url = 'http://127.0.0.1:3229') {
  await fs.mkdir(OUT, { recursive:true }); const results = [];
  for (const width of [1440,390,360]) for (const theme of ['warm','dark']) {
    const context = await browser.newContext({viewport:{width,height:width===360?640:900},timezoneId:'Asia/Shanghai',serviceWorkers:'block',reducedMotion:'reduce'});
    const escaped=[]; await context.route('**/api/**', async route => { escaped.push(route.request().url()); await route.fulfill({status:501,body:'{}'}); });
    await context.addInitScript(installFixtures);
    await context.addInitScript(installGoals);
    await context.addInitScript(theme => localStorage.setItem('psy-theme',theme), theme);
    const page = await context.newPage(); const errors=[]; page.on('pageerror',e => errors.push(String(e))); page.setDefaultTimeout(12000);
    try {
      await page.goto(url,{waitUntil:'domcontentloaded'});
      await page.getByRole('region',{name:'每日记录快捷入口'}).waitFor();
      await page.waitForFunction(() => window.dailyExperienceFixture.calls.some(c => c.path.endsWith('/events')));
      for (const module of ['module_1','module_2','module_3','module_4']) {
        await page.evaluate(m => window.dailyExperienceFixture.setModule(m), module);
        await page.getByText(`模拟对话 ${module}：可以按自己的节奏继续。`,{exact:true}).waitFor();
        if (module !== 'module_1') await page.getByRole('button',{name:/专心聊，目标、记录和档案都在这里/}).click();
        await page.getByRole('button',{name:'目标与记录',exact:true}).click();
        const dialog=page.getByRole('dialog',{name:'目标与记录',exact:true}); await dialog.waitFor();
        assert.equal(await page.locator('dialog[open]').count(),1,'one modal owns focus');
        await dialog.getByRole('tab',{name:'目标',exact:true}).waitFor();
        await dialog.getByRole('button',{name:'返回对话讨论目标'}).waitFor();
        await dialog.getByRole('button',{name:/饭后散步十分钟/}).click();
        await dialog.getByRole('heading',{name:'PA 目标卡 · 第 1 版'}).waitFor();
        await dialog.getByRole('button',{name:'← 返回目标列表',exact:true}).click();
        await dialog.getByRole('tab',{name:'每日记录',exact:true}).click();
        await dialog.getByRole('heading',{name:'每日行为记录',exact:true}).waitFor();
        await noOverflow(page);
        await dialog.getByRole('button',{name:'关闭目标与记录'}).click();
        await dialog.waitFor({state:'hidden'});
      }
      await page.evaluate(() => window.dailyExperienceFixture.setModule('module_3'));
      await page.getByRole('region',{name:'每日记录快捷入口'}).getByRole('button',{name:'记录今天'}).click();
      const dialog=page.getByRole('dialog',{name:'目标与记录',exact:true}); await dialog.waitFor();
      assert.equal(await dialog.getByRole('tab',{name:'每日记录',exact:true}).getAttribute('aria-selected'),'true');
      await dialog.getByRole('textbox',{name:'想再写一点（可留空）',exact:true}).fill('切换页签后保留的未保存内容');
      await dialog.getByRole('tab',{name:'目标',exact:true}).click();
      await dialog.getByRole('tab',{name:/每日记录/}).click();
      assert.equal(await dialog.getByRole('textbox',{name:'想再写一点（可留空）',exact:true}).inputValue(),'切换页签后保留的未保存内容');
      await page.keyboard.press('Escape');
      await dialog.getByText('每日记录还有未保存的内容，离开后将丢失。',{exact:true}).waitFor();
      await dialog.getByRole('button',{name:'继续填写',exact:true}).click();
      for (let i=0;i<3;i++) await dialog.getByRole('radio',{name:`${SUMMARY[i]} ${i+1}`,exact:true}).click();
      await page.evaluate(() => {window.dailyExperienceFixture.failSave=true;});
      await dialog.getByRole('button',{name:'保存记录',exact:true}).click();
      await dialog.getByText('模拟保存失败，请保留填写内容后重试。',{exact:true}).waitFor();
      assert.equal(await dialog.getByRole('textbox',{name:'想再写一点（可留空）',exact:true}).inputValue(),'切换页签后保留的未保存内容');
      await page.evaluate(() => {window.dailyExperienceFixture.failSave=false;});
      await dialog.getByRole('button',{name:'保存记录',exact:true}).click();
      await dialog.getByRole('heading',{name:'记录已保存',exact:true}).waitFor();
      await dialog.getByRole('button',{name:'完成，返回对话'}).click();
      await dialog.waitFor({state:'hidden'});
      await page.evaluate(() => window.dailyExperienceFixture.setModule('module_4'));
      await page.getByRole('button',{name:/专心聊，目标、记录和档案都在这里/}).click();
      await page.getByRole('button',{name:'目标与记录',exact:true}).click();
      await dialog.getByRole('tab',{name:'每日记录',exact:true}).click();
      await dialog.getByRole('button',{name:'趋势与历史',exact:true}).click();
      const record = dialog.locator('article').filter({hasText:/2020年1月2日/});
      await record.getByRole('button',{name:/2020年1月2日/}).click();
      await record.getByRole('button',{name:'修改这份记录',exact:true}).click();
      await dialog.getByRole('heading',{name:'修改行为记录',exact:true}).waitFor();
      assert.equal(await dialog.locator('input[type="date"]').inputValue(),'2020-01-02');
      await dialog.getByRole('textbox',{name:'想再写一点（可留空）',exact:true}).fill('M4修改过去记录');
      await page.evaluate(() => {window.dailyExperienceFixture.records.find(r=>r.id===2).revision_no=8;});
      await dialog.getByRole('button',{name:'保存修改',exact:true}).click();
      await dialog.getByText('修改版本不匹配',{exact:true}).waitFor();
      assert.equal(await dialog.getByRole('textbox',{name:'想再写一点（可留空）',exact:true}).inputValue(),'M4修改过去记录');
      await page.evaluate(() => {window.dailyExperienceFixture.records.find(r=>r.id===2).revision_no=7;});
      await dialog.getByRole('button',{name:'保存修改',exact:true}).click();
      await dialog.getByRole('heading',{name:'记录已保存',exact:true}).waitFor();
      const saved=await page.evaluate(()=>window.dailyExperienceFixture.lastSaved);
      assert.equal(saved.local_date,'2020-01-02'); assert.equal(saved.activities[0].emotion,0); assert.equal(saved.revision_no,8);
      await noOverflow(page); await page.screenshot({path:path.join(OUT,`daily-${theme}-${width}.png`)});
      await dialog.getByRole('tab',{name:'目标',exact:true}).click();
      await noOverflow(page); await page.screenshot({path:path.join(OUT,`goals-${theme}-${width}.png`)});
      const goalBox=await dialog.getByRole('button',{name:/饭后散步十分钟/}).boundingBox(); assert.ok(goalBox && goalBox.height>0,'goal card scroll area must remain usable');
      await dialog.getByRole('button',{name:'关闭目标与记录'}).click();
      assert.deepEqual(await page.evaluate(()=>window.dailyExperienceFixture.unknown),[]);
      assert.deepEqual(escaped,[]); assert.deepEqual(errors,[]);
      results.push({width,theme,passed:true});
    } catch(e) { await page.screenshot({path:path.join(OUT,`failure-${theme}-${width}.png`)}).catch(()=>{}); throw e; }
    finally {await context.close();}
  }
  // Administrator sidebar uses the same unified entry, with no second daily button.
  const c=await browser.newContext({viewport:{width:1440,height:900},serviceWorkers:'block'});
  await c.route('**/api/**', route=>route.fulfill({status:501,body:'{}'}));
  await c.addInitScript(installFixtures,{role:'admin'}); const p=await c.newPage();
  try {await p.goto(url,{waitUntil:'domcontentloaded'}); await p.getByRole('button',{name:'目标与记录',exact:true}).click(); await p.getByRole('dialog',{name:'目标与记录',exact:true}).waitFor(); assert.equal(await p.locator('dialog[open]').count(),1); results.push({role:'admin',passed:true});}finally{await c.close();}
  await fs.writeFile(path.join(OUT,'results.json'),JSON.stringify(results,null,2)); return results;
};
if(require.main===module)(async()=>{const{chromium}=require('playwright');const browser=await chromium.launch({headless:true,channel:'chrome'});try{console.log(JSON.stringify(await exports.run(browser,process.argv[2]),null,2));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
