// UI-only synthetic fixture. Never send requests to a production API.
const {chromium} = require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const {install} = require('./goal-ui-fixture.cjs');
(async()=>{
 const browser = await chromium.launch({headless:true,channel:'chrome'});
 const out=path.resolve('.test-tmp/m4-ui-0917'); await fs.mkdir(out,{recursive:true});
 try {for (const width of [1440,390]) {
  const context=await browser.newContext({viewport:{width,height:900},isMobile:width<600,hasTouch:width<600});
  const fixture=await install(context);fixture.selection={goal_id:'g1'};
  await context.route('**/api/auth/me',r=>r.fulfill({json:{username:'synthetic',nickname:'本地验收',profile_uuid:'qa',role:'admin',email_verified:true,email_required:false,birth_date_required:false,display_id:'test#1000'}}));
  await context.route('**/api/program/qa-chat',r=>r.fulfill({json:{enabled:true,m1_reusable:true,runtime:{current_module:'module_4',active_goal_id:'g1',active_cycle_id:'cycle',row_version:1,flow_status:'active'},goals:[{id:'g1',title:'每周五天站桩',status:'active',goal_kind:'secondary'}],draft:{id:'r1',scenario_type:'C',execution_result:1,chain_confirmation_status:'unconfirmed',phase_a:{situation:'晚饭后在家',emotion:'有点低落'},phase_b:{overt:{activity:'站桩',actual_duration_minutes:10,action_taken:true,completion_status:'complete'}},phase_c:{short_term:{emotion_change:'完成后情绪没有明显改善'},long_term:{action_willingness:null}},abc_chain_summary:'饭后有点低落，仍站桩十分钟，情绪没有明显改善。'},can_confirm:false,missing_fields:['m4_milestone_2','m4_milestone_3','m4_milestone_4','m4_milestone_5']}}));
  await context.route('**/api/admin/prompts',r=>r.fulfill({json:{prompts:[
   {key:'global',label:'GLOBAL',description:'全局',content:'全局说明',is_overridden:false},
   {key:'module_3',label:'MODULE III',description:'计划执行前记录提醒',content:'计划执行前记录提醒',is_overridden:false},
   {key:'module_4',label:'MODULE IV',description:'复盘与迭代',content:'# 模块四：复盘与迭代\n仅基于真实经历。',is_overridden:true}]}}));
  const page=await context.newPage(); const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:3001');
  if(width<600)await page.getByRole('button',{name:'切换对话侧栏'}).click();
  await page.getByRole('button',{name:'本地验收对话',exact:false}).first().click();
  await page.waitForTimeout(800);
  await page.getByRole('button',{name:/当前旅程/}).click();
  await page.getByText('本次记录草稿',{exact:true}).waitFor();
  if(await page.getByRole('button',{name:'确认复盘及下一步决定'}).count())throw Error('unconfirmed draft must not expose confirm');
  if(!(await page.getByText('实际时长（分钟）',{exact:false}).count()))throw Error('nested labels missing');
  await page.waitForTimeout(800);
  await page.screenshot({path:path.join(out,`m4-${width}.png`),scale:'css'});
  await page.getByText('还需要在聊天中明确：',{exact:false}).scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(out,`m4-${width}-bottom.png`),scale:'css'});
  const fit=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
  if(fit.scroll>fit.width)throw Error('horizontal overflow');
  await page.getByRole('button',{name:'关闭进度详情'}).click();
  if(width<600) {
    const menu=page.getByRole('button',{name:/打开.*历史|打开.*侧栏|展开.*侧栏|打开.*菜单/});
    if(await menu.count()) await menu.first().click();
  }
  // Capture prompt pane on desktop; mobile M4 content is the central changed UI.
  if(width===1440){
    await page.getByRole('button',{name:/账号菜单/}).click();
    await page.getByRole('menuitem',{name:/全局提示词管理/}).click();
    await page.getByRole('button',{name:/MODULE III/}).click();
    await page.getByRole('textbox',{name:'MODULE III内容'}).waitFor();
    await page.waitForTimeout(800);
    await page.screenshot({path:path.join(out,'module3-copy.png'),scale:'css'});
    await page.getByRole('button',{name:'关闭',exact:true}).click();
    await page.getByRole('button',{name:'切换到暗色主题'}).click();
    await page.getByRole('button',{name:/当前旅程/}).click();
    await page.waitForTimeout(800);
    await page.screenshot({path:path.join(out,'m4-dark.png'),scale:'css'});
    await page.getByRole('button',{name:'关闭进度详情'}).click();
    await page.getByRole('button',{name:'切换到暖色主题'}).click();
  }
  if(errors.length)throw Error(errors.join(';'));
  console.log(JSON.stringify({width,fit,errors,unconfirmedBlocked:true,nestedLabels:true}));
  await context.close();
 }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
