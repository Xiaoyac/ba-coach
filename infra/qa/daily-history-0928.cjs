// Isolated browser fixtures; no real accounts, conversations or API writes.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
exports.run = async function(browser, baseUrl='http://127.0.0.1:3000') {
  const out=path.resolve(__dirname,'../../.test-tmp/daily-history-0928'); await fs.mkdir(out,{recursive:true});
  const results=[];
  for (const width of [1440,390]) {
    const context=await browser.newContext({viewport:{width,height:900}});
    await context.addInitScript(()=>{
      localStorage.setItem('psy-auth-token','synthetic');
      const original=window.fetch.bind(window);
      const activity={position:0,time_slot:'19:00–20:00',activity:'散步',emotion:0,achievement:null,connection:null,enjoyment:null,importance:null,note:'原备注'};
      const fixture=window.fixture={saved:null,fail:false,records:[
        {id:1,revision_no:2,scale_version:2,local_date:'2020-01-02',timezone:'Asia/Shanghai',status:'completed',completion_rate:0,activity_level:5,overall_mood:0,completion_not_applicable:false,social_connection:null,approach_vs_avoidance:null,reflection_note:'原来的感受',activities:[activity]},
        {id:2,revision_no:1,scale_version:1,local_date:'2020-01-01',timezone:'Asia/Shanghai',status:'completed',completion_rate:9,activity_level:8,overall_mood:null,social_connection:7,approach_vs_avoidance:6,reflection_note:'旧版',activities:[]},
      ]};
      const json=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});
      const detail=()=>({session_id:'daily-demo',title:'每日记录测试',revision:1,updated_at:'2026-09-17T04:00:00Z',next_module:'module_3',messages:[{role:'assistant',content:'可以每天简短记下活动与感受。',module:'module_3',reply_module:'module_3'}]});
      window.fetch=async(input,options={})=>{
        const url=new URL(typeof input==='string'?input:input.url,location.href),p=url.pathname;
        if(p==='/api/auth/me')return json({username:'synthetic',nickname:'本地验收',role:localStorage.getItem('qa-admin')?'admin':'user',profile_uuid:'synthetic',email_required:false,email_verified:true});
        if(p==='/api/conversations')return json(options.method==='POST'?detail():[detail()]);
        if(p==='/api/conversations/daily-demo'||p==='/api/conversations/current')return json(detail());
        if(p.endsWith('/revision'))return json({revision:1});
        if(p.endsWith('/events'))return new Response(new ReadableStream({start(){},cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
        if(p==='/api/admin/assessments')return json({items:fixture.records.map(record=>({subject_id:'synthetic',username:'synthetic',display_id:'测试#12345',record})),total:2,users:1,versions:{'1':1,'2':1},has_more:false});
        if(p==='/api/admin/assessments/1/revisions')return json([
          {revision_no:2,saved_at:'2020-01-03T00:00:00Z',record:fixture.records[0]},
          {revision_no:1,saved_at:'2020-01-02T00:00:00Z',record:{...fixture.records[0],revision_no:1,reflection_note:'修改前的原始内容'}},
        ]);
        if(p==='/api/assessment/history')return json({items:fixture.records,has_more:false,next_offset:null});
        if(p==='/api/assessment/by-date')return json(fixture.records.find(r=>r.local_date===url.searchParams.get('local_date'))??null);
        if(p==='/api/assessment'||/^\/api\/assessment\/\d+$/.test(p)){
          fixture.saved={path:p,method:options.method,body:JSON.parse(options.body)};
          if(fixture.fail)return json({detail:'这份记录已在别处修改，请重新打开最新记录后再编辑。'},409);
          return json({},p==='/api/assessment'?201:200);
        }
        if(p.startsWith('/api/'))return json({});
        return original(input,options);
      };
    });
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.stack || e.message));
    await page.goto(baseUrl);
    const open=async()=>{
      await page.getByRole('button',{name:'打开每日记录',exact:true}).click();
      await page.getByRole('dialog',{name:'每日行为记录',exact:true}).waitFor();
    };
    await open();
    await page.getByLabel('记录日期',{exact:true}).fill('2020-01-02');
    await page.getByRole('button',{name:'打开这一天的记录',exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'保存记录',exact:true}).isDisabled(),true);
    await page.getByRole('button',{name:'打开这一天的记录',exact:true}).click();
    await page.getByRole('dialog',{name:'修改行为记录',exact:true}).waitFor();
    assert.equal(await page.getByLabel('活动 1 的内容',{exact:true}).inputValue(),'散步');
    assert.equal(await page.getByText('想再写一点（可留空）').locator('..').locator('textarea').inputValue(),'原来的感受');
    await page.getByLabel('记录日期',{exact:true}).fill('2020-01-04');
    await page.getByLabel('活动 1 的内容',{exact:true}).fill('慢走');
    await page.waitForFunction(()=>!document.querySelector('dialog[open] button[type="submit"]')?.disabled);
    await page.screenshot({path:path.join(out,`edit-${width}.png`),fullPage:true});
    await page.evaluate(()=>window.fixture.fail=true);
    await page.getByRole('button',{name:'保存修改',exact:true}).click();
    await page.getByRole('alert').filter({hasText:'已在别处修改'}).waitFor();
    assert.equal(await page.getByLabel('活动 1 的内容',{exact:true}).inputValue(),'慢走');
    await page.evaluate(()=>window.fixture.fail=false);
    await page.getByRole('button',{name:'保存修改',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    let saved=await page.evaluate(()=>window.fixture.saved);
    assert.equal(saved.method,'PUT'); assert.equal(saved.body.expected_revision,2);
    assert.equal(saved.body.local_date,'2020-01-04'); assert.equal(saved.body.activities[0].activity,'慢走');
    assert.equal(saved.body.activities[0].emotion,0); assert.equal(saved.body.activities[0].enjoyment,null);
    await open();
    await page.getByRole('button',{name:'查看历史',exact:true}).click();
    await page.getByRole('button',{name:/1月1日/}).click();
    await page.getByRole('button',{name:'修改这份记录',exact:true}).click();
    await page.getByRole('dialog',{name:'修改行为记录',exact:true}).waitFor();
    await page.getByText('这份记录使用旧版').waitFor();
    await page.waitForFunction(()=>!document.querySelector('dialog[open] button[type="submit"]')?.disabled);
    await page.screenshot({path:path.join(out,`legacy-${width}.png`),fullPage:true});
    await page.getByRole('button',{name:'保存修改',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    saved=await page.evaluate(()=>window.fixture.saved);
    assert.equal(saved.body.summary.completion_rate,9);assert.equal(saved.body.summary.social_connection,7);
    assert.equal(saved.body.summary.overall_mood,null);assert.equal(saved.body.activities.length,0);
    await open();
    await page.getByLabel('记录日期',{exact:true}).fill('2020-01-06');
    await page.getByRole('radio',{name:'想做的事情完成程度 2',exact:true}).click();
    await page.getByRole('radio',{name:'这一天总体身体活动程度 3',exact:true}).click();
    await page.getByRole('radio',{name:'这一天整体心情如何？ 4',exact:true}).click();
    await page.getByRole('button',{name:'保存记录',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    saved=await page.evaluate(()=>window.fixture.saved);
    assert.equal(saved.method,'POST');assert.equal(saved.body.local_date,'2020-01-06');
    assert.equal(saved.body.activities.length,0);
    await page.evaluate(()=>localStorage.setItem('qa-admin','true'));
    await page.reload();
    await page.getByRole('button',{name:/账号菜单/}).click();
    await page.getByRole('menuitem',{name:'每日记录数据',exact:true}).click();
    const admin=page.getByRole('dialog',{name:'每日记录数据',exact:true});
    await admin.getByRole('button',{name:'查看 测试#12345 2020-01-02',exact:true}).click();
    await admin.getByRole('button',{name:'查看修改历史',exact:true}).click();
    await admin.getByRole('button',{name:/第 1 版 · 记录日期/}).click();
    await admin.getByText('修改前的原始内容',{exact:true}).waitFor();
    assert.equal(await admin.getByRole('button',{name:'修改这份记录',exact:true}).count(),0);
    await page.screenshot({path:path.join(out,`admin-history-${width}.png`),fullPage:true});
    await admin.getByRole('button',{name:'查看当前记录',exact:true}).click();
    await admin.getByText('原来的感受',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);results.push({width,passed:true});await context.close();
  }
  return results;
};
