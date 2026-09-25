// QA inventory: required/optional, zero/null and historical N/A, partial cards, error retry,
// history versions, M3 entry, compact confirmation with keyboard, responsive themes.
// Synthetic browser API only; backend persistence is separately covered by pytest.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
exports.run = async function(browser, baseUrl = 'http://127.0.0.1:3000') {
  const out = path.resolve(__dirname,'../../.test-tmp/daily-record-0917'); await fs.mkdir(out,{recursive:true});
  const reports=[];
  for (const [width,theme,height=900] of [[1440,'warm'],[390,'warm'],[360,'warm'],[1440,'dark'],[390,'dark'],[1366,'warm',768],[1024,'warm',768],[820,'warm'],[360,'warm',740]]) {
    const context = await browser.newContext({viewport:{width,height},isMobile:width<600,hasTouch:width<600});
    try {
    await context.addInitScript(({theme}) => {
      localStorage.setItem('psy-auth-token','synthetic'); localStorage.setItem('psy-theme',theme);
      const original=window.fetch.bind(window);
      const f=window.fixture={submitted:null,fail:true,confirmed:null};
      const program={enabled:true,can_confirm:true,record_hash:'fixture-hash',draft:{id:'draft',record_requirement:'活动时间、内容和心情'},
        runtime:{current_module:'module_3',row_version:7,active_goal_id:null,active_cycle_id:null,flow_status:'discussing'}};
      const detail=()=>({session_id:'daily-demo',title:'每日记录验收',revision:1,updated_at:'2026-09-17T04:00:00Z',next_module:'module_3',
        messages:[{role:'assistant',content:'可以每天简短记下活动与感受。',module:'module_3',reply_module:'module_3'}]});
      const json=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});
      window.fetch=async(input,options={})=>{
        const url=new URL(typeof input==='string'?input:input.url,location.href).pathname;
        if(url==='/api/auth/me')return json({username:'synthetic',nickname:'本地验收',role:theme==='dark'?'admin':'user',profile_uuid:'synthetic',email_required:false,email_verified:true});
        if(url==='/api/conversations')return json([detail()]);
        if(url==='/api/conversations/daily-demo')return json(detail());
        if(url.endsWith('/revision'))return json({revision:1});
        if(url.endsWith('/events'))return new Response(new ReadableStream({start(){},cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
        if(url==='/api/program/daily-demo')return json(program);
        if(url==='/api/program/daily-demo/confirm'){
          f.confirmed=JSON.parse(options.body);program.can_confirm=false;return json(program);
        }
        if(url==='/api/assessment'){
          f.submitted=JSON.parse(options.body);return json({},f.fail?503:201);
        }
        if(url==='/api/assessment/history')return json({has_more:false,next_offset:null,items:[
          {id:2,scale_version:2,local_date:'2026-09-17',status:'completed',completion_rate:null,completion_not_applicable:true,
            activity_level:5,overall_mood:0,activities:[{time_slot:'19:00–20:00',activity:'散步',emotion:0,achievement:null,connection:null,enjoyment:null,importance:null}]},
          {id:1,scale_version:1,local_date:'2026-09-16',status:'completed',completion_rate:7,activity_level:6,overall_mood:7,social_connection:5,approach_vs_avoidance:8,activities:[]},
        ]});
        if(url.startsWith('/api/'))return json({});
        return original(input,options);
      };
    },{theme});
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    const shot=async name=>{await page.waitForTimeout(250);await page.screenshot({path:path.join(out,`${name}-${width}-${theme}${height===900?'':`-${height}`}.png`),scale:'css'});};
    await page.goto(baseUrl);
    const entry=page.getByRole('button',{name:'打开每日记录',exact:true});await entry.waitFor();
    // The single navigation entry replaces the header action and history shortcut.
    const rail=page.getByRole('navigation',{name:'对话历史'});
    const navEntry=rail.getByRole('button',{name:'记录今日',exact:true});
    const sidebarToggle=page.getByRole('button',{name:'切换对话侧栏'});
    assert.equal(await page.getByRole('button',{name:'我的每日记录',exact:true}).count(),0);
    assert.equal(await page.locator('header').getByRole('button',{name:'记录今日',exact:true}).count(),0);
    assert.equal(await page.getByRole('button',{name:'记录今日',exact:true,includeHidden:true}).count(),1);
    if(width<768) await sidebarToggle.click();
    else {
      await sidebarToggle.click();await rail.waitFor({state:'hidden'});
      await sidebarToggle.click();
    }
    await navEntry.waitFor({state:'visible'});
    await page.waitForTimeout(350); // Allow the mobile drawer slide to settle before measuring.
    const navBounds=await navEntry.boundingBox();
    assert.ok(navBounds.x>=0 && navBounds.y>=0 && navBounds.y+navBounds.height<=height);
    assert.ok(navBounds.height>=44);
    await shot('sidebar-record-entry');
    await navEntry.click();
    const navDialog=page.getByRole('dialog',{name:'今天的行为记录'});
    await navDialog.waitFor();
    if(width<768) await rail.waitFor({state:'hidden'});
    await navDialog.getByRole('button',{name:'查看历史'}).click();
    const navHistory=page.getByRole('dialog',{name:'历史每日记录'});
    await navHistory.getByText('旧版量表',{exact:false}).waitFor();
    await page.keyboard.press('Escape');await navHistory.waitFor({state:'hidden'});
    // Reopening through navigation always starts with today's record, not history.
    if(width<768) await sidebarToggle.click();
    await navEntry.click();await navDialog.waitFor();
    await page.keyboard.press('Escape');await navDialog.waitFor({state:'hidden'});
    assert.equal(await page.getByText('当前旅程',{exact:true}).count(),0);
    await page.getByRole('button',{name:/账号菜单/}).click();
    assert.equal(await page.getByRole('menuitem',{name:'每日记录数据',exact:true}).count(),theme==='dark'?1:0);
    await page.getByRole('menuitem',{name:'本次记录与确认',exact:true}).click();
    const programDialog=page.getByRole('dialog',{name:'本次记录与确认'});
    await programDialog.waitFor();
    const confirm=programDialog.getByRole('button',{name:'确认记录方式，开始执行'});
    assert.equal(await confirm.isDisabled(),true);
    await programDialog.getByRole('checkbox').check();assert.equal(await confirm.isEnabled(),true);
    await shot('compact-confirmation');await confirm.click();await programDialog.waitFor({state:'hidden'});
    assert.deepEqual(await page.evaluate(()=>window.fixture.confirmed),{record_id:'draft',record_hash:'fixture-hash',row_version:7});
    await entry.click();
    const dialog=page.getByRole('dialog',{name:'今天的行为记录'});
    const save=dialog.getByRole('button',{name:'保存今日记录',exact:true});
    assert.equal(await save.isDisabled(),true);
    assert.equal(await dialog.getByRole('radio',{checked:true}).count(),0);
    assert.equal(await dialog.getByRole('radiogroup').count(),8);
    assert.equal(await dialog.locator('details').count(),0);
    assert.equal(await dialog.getByText('几点到几点',{exact:true}).count(),0);
    assert.equal(await dialog.getByRole('combobox',{name:'活动 1 的时间段'}).innerText(),'选择活动时间');
    const contentBox=dialog.getByRole('textbox',{name:'活动 1 的内容'});
    assert.equal(await contentBox.getAttribute('aria-required'),'true');
    const contentBounds=await contentBox.boundingBox();
    assert.ok(contentBounds.height>=60 && contentBounds.height<=80);
    const timeBounds=await dialog.getByRole('combobox',{name:'活动 1 的时间段'}).boundingBox();
    assert.ok(Math.abs(timeBounds.width-contentBounds.width)<2);
    assert.equal(await dialog.locator('[data-activity-required] > div').count(),3);
    const activityPanel=dialog.getByRole('region',{name:'活动 1',exact:true});
    const summaryPanel=dialog.locator('[data-daily-summary-panel]');
    if(width>=1024) {
      const a=await activityPanel.boundingBox(), b=await summaryPanel.boundingBox();
      assert.ok(Math.abs(a.width-b.width)<2,'desktop columns should be equal width');
      assert.ok(Math.abs(a.y-b.y)<2,'panels start on the same baseline');
    }
    const labelStyles=await dialog.locator('[data-activity-required]').evaluate(el=>Array.from(el.querySelectorAll('p,label,[data-activity-mood] > div > div > span:first-child')).slice(0,3).map(n=>{
      const s=getComputedStyle(n);return [s.fontSize,s.fontWeight,s.lineHeight];
    }));
    assert.equal(labelStyles.length,3);
    assert.deepEqual(labelStyles[0],labelStyles[1]);assert.deepEqual(labelStyles[1],labelStyles[2]);
    const optionalArea=dialog.getByRole('region',{name:'其他感受（可选）'});
    await optionalArea.getByText('4项选填',{exact:true}).waitFor();
    assert.equal(await optionalArea.getByText('不填也可以',{exact:false}).count(),0);
    assert.equal(await optionalArea.getByRole('radiogroup').count(),4);
    assert.ok((await optionalArea.getByRole('radio').first().boundingBox()).width>=24);
    const moodSize=await dialog.getByRole('radio',{name:'做完活动后的心情 0',exact:true}).boundingBox();
    assert.equal(moodSize.height,44);
    assert.equal((await dialog.getByRole('radio',{name:'今天总体身体活动程度 0',exact:true}).boundingBox()).height,moodSize.height);
    const saveBounds=await save.boundingBox();assert.ok(saveBounds.y>=0 && saveBounds.y+saveBounds.height<=height);
    assert.ok((await dialog.boundingBox()).width<=width);
    await shot('required-empty');
    await dialog.getByRole('textbox',{name:'活动 1 的内容'}).fill('散步');
    await dialog.getByRole('combobox',{name:'活动 1 的时间段'}).click();
    const picker=dialog.getByRole('dialog',{name:'活动 1 的时间段'});
    const pickerBounds=await picker.boundingBox();
    assert.ok(pickerBounds.x>=0 && pickerBounds.x+pickerBounds.width<=width);
    await shot('time-picker');
    await dialog.getByRole('listbox',{name:'开始',exact:true}).getByRole('option',{name:'19:00',exact:true}).click();
    await dialog.getByRole('listbox',{name:'结束',exact:true}).getByRole('option',{name:'20:00',exact:true}).click();
    await dialog.getByRole('radio',{name:'做完活动后的心情 0',exact:true}).click();
    await dialog.getByRole('radio',{name:'今天总体身体活动程度 5',exact:true}).click();
    await dialog.getByRole('radio',{name:'回顾今天，你今天整体心情如何？ 0',exact:true}).click();
    assert.equal(await save.isDisabled(),true,'completion must be explicitly rated');
    assert.equal(await dialog.getByRole('checkbox').count(),0);
    assert.equal(await dialog.getByText(/不适用|预定计划|整体／平均/).count(),0);
    await dialog.getByRole('radio',{name:'想做的事情完成程度 0',exact:true}).click();
    assert.equal(await save.isEnabled(),true);
    await optionalArea.scrollIntoViewIfNeeded();
    const optional=dialog.getByRole('radio',{name:'成就 0',exact:true});await optional.click();
    assert.equal(await optional.getAttribute('aria-checked'),'true');await optional.click();
    assert.equal(await optional.getAttribute('aria-checked'),'false');
    await shot('optional-always-visible');
    await dialog.getByRole('button',{name:'添加一项活动'}).click();
    assert.equal(await save.isEnabled(),true);
    await dialog.getByRole('textbox',{name:'活动 2 的内容'}).fill('半填记录');assert.equal(await save.isDisabled(),true);
    await dialog.getByRole('button',{name:'删除活动 2'}).click();assert.equal(await save.isEnabled(),true);
    await save.click();await dialog.getByRole('alert').waitFor();
    assert.equal(await dialog.getByRole('textbox',{name:'活动 1 的内容'}).inputValue(),'散步');
    await dialog.getByRole('heading',{name:'回看这一天'}).scrollIntoViewIfNeeded();await shot('summary-required');
    await page.evaluate(()=>window.fixture.fail=false);await save.click();await dialog.waitFor({state:'hidden'});
    const body=await page.evaluate(()=>window.fixture.submitted);
    assert.equal(body.activities.length,1);assert.equal(body.activities[0].emotion,0);
    for(const key of ['achievement','connection','enjoyment','importance'])assert.equal(body.activities[0][key],null);
    assert.deepEqual(body.summary,{completion_rate:0,completion_not_applicable:false,activity_level:5,overall_mood:0,reflection_note:null});
    // Daily summary is independently valid; starting an activity makes only
    // that row subject to its own required fields. Deleting it restores the
    // summary-only path, including when it is the sole activity card.
    await entry.click();
    await dialog.getByRole('radio',{name:'今天总体身体活动程度 5',exact:true}).click();
    await dialog.getByRole('radio',{name:'回顾今天，你今天整体心情如何？ 0',exact:true}).click();
    assert.equal(await save.isDisabled(),true,'summary still needs completion rating');
    await dialog.getByRole('radio',{name:'想做的事情完成程度 0',exact:true}).click();
    assert.equal(await save.isEnabled(),true,'summary alone can be saved');
    await dialog.getByRole('textbox',{name:'活动 1 的备注'}).fill('半填记录');
    assert.equal(await save.isDisabled(),true,'even a note starts activity-row validation');
    await dialog.getByRole('button',{name:'删除活动 1',exact:true}).click();
    assert.equal(await save.isEnabled(),true,'removing the only partial activity restores summary-only');
    await save.click();await dialog.waitFor({state:'hidden'});
    assert.deepEqual((await page.evaluate(()=>window.fixture.submitted)).activities,[]);
    await entry.click();await dialog.getByRole('button',{name:'查看历史'}).click();
    const history=page.getByRole('dialog',{name:'历史每日记录'});
    await history.getByText('不适用',{exact:true}).waitFor();
    await history.getByText('旧版量表',{exact:false}).waitFor();
    assert.ok((await history.innerText()).includes('/5'));assert.ok((await history.innerText()).includes('/10'));
    await history.getByText('未填写',{exact:false}).first().waitFor();await shot('history');
    await page.keyboard.press('Escape');await history.waitFor({state:'hidden'});
    const fit=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    assert.equal(fit.width,fit.scroll);assert.deepEqual(errors,[]);
    reports.push({width,height,theme,balancedLayout:true,consistentRequired:true,requiredOptional:true,zeroNullHistoricalNA:true,newSummaryCopy:true,summaryOnly:true,partialCard:true,retry:true,historyVersions:true,m3Entry:true,confirmation:true,fit,errors});
    } finally {await context.close();}
  }
  return reports;
};
if(require.main===module) (async()=>{
  const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {for(const result of await exports.run(browser))console.log(JSON.stringify(result));}
  finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
