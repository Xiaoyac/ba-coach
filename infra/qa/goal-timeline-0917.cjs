// Local UI-only acceptance; every API request is intercepted, no user data.
// Run directly or reuse the persistent browser with exports.run(browser).
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {install} = require('./goal-ui-fixture.cjs');
const root = path.resolve(__dirname, '../..');
exports.run = async function(browser, baseUrl = 'http://127.0.0.1:3000') {
  const out = path.join(root, '.test-tmp/goal-timeline-0917');
  await fs.mkdir(out, {recursive:true});
  const reports = [];
  for (const [width,theme,height=900] of [[1440,'warm'],[390,'warm'],[360,'warm'],[1440,'dark'],[390,'dark'],[360,'warm',540]]) {
    const context = await browser.newContext({viewport:{width,height},isMobile:width<600,hasTouch:width<600});
    try {
      const fixture = await install(context);
      fixture.overview.goals[0].created_at='2026-09-10T08:00:00Z';
      fixture.overview.goals[0].updated_at='2026-09-17T08:00:00Z';
      fixture.overview.goals[1].created_at='2026-09-15T08:00:00Z';
      fixture.overview.goals[2].created_at='2026-09-01T08:00:00Z';
      fixture.overview.goals.push({id:'undated',title:'缺少日期的旧档案',status:'completed',goal_kind:'secondary'});
      const originalGoals = structuredClone(fixture.overview.goals);
      await context.addInitScript(theme=>localStorage.setItem('psy-theme',theme),theme);
      await context.route('**/api/program/goals/*/history?*',async route=>{
        const url=new URL(route.request().url()), page=Number(url.searchParams.get('page'));
        const id=url.pathname.split('/')[4];
        await route.fulfill({json:{goal:fixture.overview.goals.find(g=>g.id===id),page,page_size:12,
          totals:{plans:2,cycles:14,activities:1},
          plans:page===1?[2,1].map(n=>({id:'p'+n,version_no:n,record_status:n===2?'confirmed':'superseded',confirmation_status:'confirmed',activity_content:'站桩十分钟',schedule_text:'每周五天',created_at:'2026-09-10T08:00:00Z',updated_at:'2026-09-15T08:00:00Z'})):[],
          cycles:Array.from({length:page===1?12:2},(_,i)=>({id:'c'+((page===1?14:2)-i),ordinal:(page===1?14:2)-i,status:'completed',plan_version:1,activity_content:'站桩十分钟',schedule_text:'每周五天',created_at:'2026-09-10T08:00:00Z',started_at:'2026-09-11T08:00:00Z',completed_at:'2026-09-15T08:00:00Z',review_status:'confirmed',review_summary:'保留弹性，按自己的节奏继续。',review_action:'continue'})),
          activities:page===1?[{id:'a1',activity_content:'站桩',event_kind:'performed',status:'active',created_at:'2026-09-11T08:00:00Z',effect:'放松一些',cycle_ordinal:1}]:[]}});
      });
      const page=await context.newPage(), errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      const shot=async name=>{await page.screenshot({path:path.join(out,`${name}-${width}-${theme}${height===900?'':`-${height}`}.png`),scale:'css'});};
      await page.goto(baseUrl);
      // Sidebar is collapsed on mobile.
      const entry=page.getByRole('button',{name:'我的目标',exact:true});
      const openArchive=async()=>{
        await page.getByRole('button',{name:'切换对话侧栏',exact:true}).waitFor();
        if (!(await entry.isVisible())) await page.getByRole('button',{name:'切换对话侧栏',exact:true}).click();
        await entry.click();
      };
      await openArchive();
      const dialog=page.getByRole('dialog',{name:'我的目标',exact:true});
      const choose=async(label,value)=>{
        await dialog.getByRole('combobox',{name:label,exact:true}).click();
        await dialog.getByRole('listbox',{name:label,exact:true}).locator(`[data-value="${value}"]`).click();
      };
      const timeline=dialog.getByRole('list',{name:'目标设定时间线'});
      await timeline.waitFor();
      const ids=()=>timeline.locator('[data-goal-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.goalId));
      assert.deepEqual(await ids(),['g2','g1','g3','undated']);
      assert.deepEqual(await timeline.locator('[data-goal-number]').evaluateAll(nodes=>nodes.map(n=>n.dataset.goalNumber)),['3','2','1','unknown']);
      await shot('timeline-newest');
      const order=dialog.getByRole('combobox',{name:'时间线顺序'});
      await order.click();
      const orderMenu=dialog.getByRole('listbox',{name:'时间线顺序'});
      await orderMenu.waitFor();
      assert.equal(await dialog.locator('select').count(),0);
      await shot('themed-order-menu');
      const menuBounds=await orderMenu.boundingBox();
      assert.ok(menuBounds.x>=0 && menuBounds.x+menuBounds.width<=width+1);
      assert.ok(menuBounds.y>=0 && menuBounds.y+menuBounds.height<=height);
      await order.press('End');await order.press('Escape');
      assert.equal(await order.getAttribute('aria-expanded'),'false');
      assert.deepEqual(await ids(),['g2','g1','g3','undated']);
      assert.equal(await dialog.isVisible(),true);
      await order.press('ArrowDown');await order.press('End');await order.press('Enter');
      assert.deepEqual(await ids(),['g3','g1','g2','undated']);
      await shot('timeline-from-first');
      await dialog.locator('summary').filter({hasText:'筛选目标'}).click();
      await dialog.getByRole('combobox',{name:'目标状态',exact:true}).click();
      await shot('themed-status-menu');
      const statusBounds=await dialog.getByRole('listbox',{name:'目标状态'}).boundingBox();
      assert.ok(statusBounds.x>=0 && statusBounds.x+statusBounds.width<=width+1);
      assert.ok(statusBounds.y>=0 && statusBounds.y+statusBounds.height<=height);
      if(height<=600)assert.ok(statusBounds.y+statusBounds.height<=(await dialog.getByRole('combobox',{name:'目标状态',exact:true}).boundingBox()).y);
      await dialog.getByRole('listbox',{name:'目标状态'}).locator('[data-value="active"]').click();
      assert.deepEqual(await ids(),['g1','g2']);
      assert.deepEqual(await timeline.locator('[data-goal-number]').evaluateAll(nodes=>nodes.map(n=>n.dataset.goalNumber)),['2','3']);
      await choose('目标类型','secondary');
      assert.deepEqual(await ids(),['g2']);
      await dialog.getByRole('textbox',{name:'搜索目标'}).fill('不存在');
      await dialog.getByText('没有找到匹配的目标',{exact:true}).waitFor();
      await dialog.getByRole('button',{name:'清除筛选'}).click();
      assert.deepEqual(await ids(),['g3','g1','g2','undated']);
      await dialog.locator('summary').filter({hasText:'筛选目标'}).click();
      const card=dialog.locator('[data-goal-id="g1"]');
      await card.scrollIntoViewIfNeeded();
      const before=await card.evaluate(n=>n.closest('.zen-scroll').scrollTop);
      await card.click();
      await dialog.getByRole('heading',{name:'PA 目标卡 · 第 2 版'}).waitFor();
      await dialog.getByRole('button',{name:/^执行与复盘/}).click();
      await dialog.getByRole('heading',{name:'第 14 轮',exact:true}).waitFor();
      await shot('cycle-details');
      await dialog.getByRole('button',{name:'下一页',exact:true}).click();
      await dialog.getByRole('heading',{name:'第 2 轮',exact:true}).waitFor();
      assert.equal(await dialog.getByRole('heading',{name:'第 1 轮',exact:true}).count(),1);
      await dialog.getByRole('button',{name:/^活动记录/}).click();
      await dialog.getByRole('heading',{name:'站桩',exact:true}).waitFor();
      await dialog.getByRole('button',{name:'← 返回目标列表'}).click();
      assert.deepEqual(await ids(),['g3','g1','g2','undated']);
      assert.ok(Math.abs(await card.evaluate(n=>n.closest('.zen-scroll').scrollTop)-before)<4);
      assert.equal(await card.evaluate(n=>n===document.activeElement),true);
      await dialog.getByRole('button',{name:'卡片总览',exact:true}).click();
      assert.equal(await dialog.locator('.goal-archive-card').count(),4);
      await choose('排序方式','title');
      await shot('card-overview');
      await dialog.getByRole('button',{name:'时间线',exact:true}).click();
      assert.deepEqual(await ids(),['g3','g1','g2','undated']);
      await order.click();await dialog.getByRole('heading',{name:'我的目标',exact:true}).click();
      await orderMenu.waitFor({state:'hidden'});
      await dialog.locator('[role="combobox"][aria-label="时间线顺序"][aria-expanded="false"]').waitFor();
      assert.equal(await order.getAttribute('aria-expanded'),'false');
      await order.click();await order.press('Tab');
      assert.equal(await order.getAttribute('aria-expanded'),'false');
      const fit=await dialog.evaluate(n=>({w:n.clientWidth,scroll:n.scrollWidth,bottom:n.getBoundingClientRect().bottom,viewport:innerHeight}));
      assert.ok(fit.scroll<=fit.w+1); assert.ok(fit.bottom<=fit.viewport+1);
      await dialog.getByRole('button',{name:'关闭目标总览'}).click();
      fixture.failOverview=true;
      await openArchive();await dialog.getByText('加载未完成',{exact:true}).waitFor();
      fixture.failOverview=false;await dialog.getByRole('button',{name:'重新加载'}).click();await timeline.waitFor();
      await dialog.getByRole('button',{name:'关闭目标总览'}).click();
      fixture.overview.goals=[];await openArchive();await dialog.getByText('还没有目标，先从聊聊开始',{exact:true}).waitFor();
      await shot('empty-archive');
      assert.equal(fixture.requests.filter(r=>r.method!=='GET').length,0);
      fixture.overview.goals=originalGoals;
      assert.deepEqual(errors,[]);
      reports.push({width,height,theme,themedMenus:true,keyboardAndDismiss:true,chronological:true,stableNumbers:true,filters:true,detailsAndPagination:true,backPosition:true,emptyAndRetry:true,readOnly:true,fit,errors});
    } finally {await context.close();}
  }
  return reports;
};
if(require.main===module) (async()=>{
  const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {for(const report of await exports.run(browser))console.log(JSON.stringify(report));}
  finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
