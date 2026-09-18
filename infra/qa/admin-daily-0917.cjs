// QA: admin gating, filters/reset/version/date, pagination, details/back,
// export both formats, dirty-filter protection, empty/error/retry, themes and fit.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');const path=require('node:path');
const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const out=path.resolve('.test-tmp/admin-daily-0917');await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{for(const [width,theme] of [[1440,'warm'],[390,'warm'],[360,'warm'],[1440,'dark'],[390,'dark']]){
  const context=await browser.newContext({viewport:{width,height:900},isMobile:width<600,hasTouch:width<600,acceptDownloads:true});
  await context.addInitScript(({theme})=>{
   localStorage.setItem('psy-auth-token','synthetic');localStorage.setItem('psy-theme',theme);
   const original=window.fetch.bind(window);const f=window.fixture={fail:false,calls:[],exports:[]};
   const rows=Array.from({length:23},(_,i)=>({subject_id:`subject-${i%3}`,username:`person${i%3}`,display_id:['小河#12345','小山#23456','清风#34567'][i%3],record:{
    id:i+1,scale_version:i===1?1:2,local_date:`2026-09-${String(17-Math.floor(i/3)).padStart(2,'0')}`,status:'completed',timezone:'Asia/Shanghai',
    completion_rate:i===0?null:i===1?7:3,completion_not_applicable:i===0,activity_level:i===1?6:5,overall_mood:i===1?7:0,social_connection:3,approach_vs_avoidance:4,
    reflection_note:'完成之后觉得轻松一点。',activities:[{position:0,time_slot:'19:00–20:00',activity:'散步，与朋友',emotion:0,achievement:null,connection:3,enjoyment:null,importance:null,note:'今天先走了一小段'}]
   }}));
   const conversation={session_id:'demo',title:'数据管理验收',revision:1,messages:[],updated_at:'2026-09-17T00:00:00Z'};
   const json=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});
   window.fetch=async(input,options={})=>{
    const u=new URL(typeof input==='string'?input:input.url,location.href),url=u.pathname;
    if(url==='/api/auth/me')return json({username:'synthetic',nickname:'管理员验收',role:localStorage.getItem('qa-user')?'user':'admin',profile_uuid:'synthetic',email_required:false,email_verified:true});
    if(url==='/api/conversations')return json([conversation]);
    if(url==='/api/conversations/demo')return json(conversation);
    if(url.endsWith('/revision'))return json({revision:1});
    if(url.endsWith('/events'))return new Response(new ReadableStream({start(){},cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
    if(url==='/api/program/demo')return json({enabled:false});
    if(url==='/api/admin/assessments/export.csv'){f.exports.push(Object.fromEntries(u.searchParams));return new Response('\ufeffrecord_id,scale_version\r\n1,2\r\n',{headers:{'Content-Type':'text/csv'}});}
    if(url==='/api/admin/assessments'){
     const p=Object.fromEntries(u.searchParams);f.calls.push(p);
     if(f.fail)return json({detail:'模拟读取失败，请重试'},503);
     const matched=rows.filter(r=>(!p.query||r.username.includes(p.query)||r.display_id.includes(p.query))&&(!p.start||r.record.local_date>=p.start)&&(!p.end||r.record.local_date<=p.end)&&(!p.scale_version||r.record.scale_version===Number(p.scale_version)));
     const offset=Number(p.offset||0);return json({items:matched.slice(offset,offset+20),total:matched.length,users:new Set(matched.map(r=>r.subject_id)).size,versions:{'1':matched.filter(r=>r.record.scale_version===1).length,'2':matched.filter(r=>r.record.scale_version===2).length},has_more:offset+20<matched.length});
    }
    if(url.startsWith('/api/'))return json({});return original(input,options);
   };
  },{theme});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const shot=async name=>{await page.waitForTimeout(250);await page.screenshot({path:path.join(out,`${name}-${width}-${theme}.png`),scale:'css'});};
  await page.goto('http://127.0.0.1:3000');await page.getByRole('button',{name:/账号菜单/}).click();
  await page.getByRole('menuitem',{name:'每日记录数据',exact:true}).click();
  const d=page.getByRole('dialog',{name:'每日记录数据',exact:true});await d.getByText('23',{exact:true}).waitFor();
  assert.equal(await d.getByRole('button',{name:/^查看 /}).count(),20);
  await shot('overview');
  await d.getByRole('button',{name:/^查看 /}).first().click();
  const detail=d.getByRole('complementary',{name:'记录详情'});await detail.getByText('散步，与朋友',{exact:true}).waitFor();
  await detail.getByText('不适用',{exact:true}).waitFor();await shot('detail');
  if(width<1024)await d.getByRole('button',{name:'返回记录列表'}).click();
  await d.getByRole('button',{name:'下一页'}).click();await d.getByText('每页 20 份 · 第 2 页').waitFor();
  assert.equal(await d.getByRole('button',{name:/^查看 /}).count(),3);
  await d.getByRole('button',{name:'上一页'}).click();
  await d.getByRole('textbox',{name:'查找用户'}).fill('person0');
  assert.equal(await d.getByRole('button',{name:'导出每日汇总'}).isDisabled(),true);
  await d.getByRole('button',{name:'查询',exact:true}).click();await d.getByText('8',{exact:true}).waitFor();
  for(const [button,kind] of [['导出每日汇总','daily'],['导出活动明细','activities']]){
   const downloadPromise=page.waitForEvent('download');await d.getByRole('button',{name:button}).click();const download=await downloadPromise;
   assert.equal(download.suggestedFilename(),`daily-records-${kind}.csv`);
   assert.equal(await page.evaluate(()=>window.fixture.exports.at(-1).query),'person0');
  }
  await d.getByRole('button',{name:'重置',exact:true}).click();await d.getByText('23',{exact:true}).waitFor();
  await d.getByRole('combobox',{name:'量表版本'}).selectOption('1');await d.getByRole('button',{name:'查询',exact:true}).click();
  await d.getByText('1',{exact:true}).first().waitFor();assert.equal(await d.getByRole('button',{name:/^查看 /}).count(),1);
  await d.getByRole('button',{name:/^查看 /}).first().click();assert.match(await detail.innerText(),/7\/10/);await shot('legacy');
  await d.getByRole('button',{name:'重置',exact:true}).click();
  await d.getByLabel('开始日期',{exact:true}).fill('2026-09-18');await d.getByLabel('结束日期',{exact:true}).fill('2026-09-17');
  await d.getByRole('button',{name:'查询',exact:true}).click();await d.getByRole('alert').filter({hasText:'开始日期不能晚于结束日期'}).waitFor();
  await d.getByLabel('开始日期',{exact:true}).fill('2026-09-17');await d.getByRole('button',{name:'查询',exact:true}).click();await d.getByText('3',{exact:true}).first().waitFor();
  await d.getByRole('textbox',{name:'查找用户'}).fill('nobody');await d.getByRole('button',{name:'查询',exact:true}).click();
  await d.getByText('没有符合筛选条件的记录。',{exact:false}).waitFor();await shot('empty');
  await page.evaluate(()=>window.fixture.fail=true);await d.getByRole('button',{name:'刷新',exact:true}).click();await d.getByRole('alert').filter({hasText:'模拟读取失败'}).waitFor();
  await page.evaluate(()=>window.fixture.fail=false);await d.getByRole('button',{name:'重新加载'}).click();await d.getByText('没有符合筛选条件的记录。',{exact:false}).waitFor();
  await d.getByRole('button',{name:'重置',exact:true}).click();await d.getByText('23',{exact:true}).waitFor();
  const fit=await d.evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth,viewport:innerWidth,doc:document.documentElement.scrollWidth}));
  assert.equal(fit.viewport,fit.doc);assert.ok(fit.scroll<=fit.width+1);assert.deepEqual(errors,[]);
  await page.keyboard.press('Escape');await d.waitFor({state:'hidden'});
  await page.evaluate(()=>localStorage.setItem('qa-user','true'));await page.reload();await page.getByRole('button',{name:/账号菜单/}).click();
  assert.equal(await page.getByRole('menuitem',{name:'每日记录数据',exact:true}).count(),0);
  console.log(JSON.stringify({width,theme,filters:true,pages:true,details:true,exports:true,retry:true,adminOnly:true,fit,errors}));
  await context.close();
 }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
