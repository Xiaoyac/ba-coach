const assert=require('node:assert/strict');
const {installFixtures}=require('./daily-experience-1002.cjs');
function isolationFixtures(){
  const original=window.fetch.bind(window),json=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
  const f=window.goalsIsolation={owner:'first',calls:[],bad:[]};
  const detail=sid=>({session_id:sid,title:`隔离会话${sid}`,revision:1,next_module:'module_2',updated_at:'2026-10-09T10:00:00Z',messages:[{id:1,role:'assistant',content:`${f.owner}账户的${sid}会话`}],pinned:false});
  const goal=sid=>({id:`${f.owner}-${sid}-goal`,title:`${f.owner}-${sid}-散步`,status:'active',goal_kind:'primary',created_at:'2026-10-01T10:00:00Z',updated_at:'2026-10-02T10:00:00Z',plan:{activity_content:`${f.owner}-${sid}-散步`,schedule_text:'每天晚饭后',location:'小区',duration_minutes:10}});
  window.fetch=async(input,options={})=>{
    const url=new URL(typeof input==='string'?input:input.url,location.href),p=url.pathname;f.calls.push({path:p,query:url.search,owner:f.owner});
    if(p==='/api/auth/me')return json({username:f.owner,nickname:f.owner,role:'admin',profile_uuid:f.owner,email_required:false,email_verified:true,birth_date_required:false});
    if(p==='/api/conversations')return json([detail('alpha'),detail('beta')]);
    if(p==='/api/conversations/current')return json(detail('alpha'));
    if(/^\/api\/conversations\/(alpha|beta)$/.test(p))return json(detail(p.split('/')[3]));
    if(p.endsWith('/context'))return json({estimated_tokens:20,retained_messages:1,summarized_messages:0,total_messages:1,mode:'compression'});
    if(p.endsWith('/revision'))return json({revision:1});
    if(p.endsWith('/events'))return new Response(new ReadableStream({start(){}}),{headers:{'Content-Type':'text/event-stream'}});
    if(p==='/api/program/goals/overview'){
      const sid=url.searchParams.get('session_id');if(!['alpha','beta'].includes(sid))f.bad.push({p,sid});
      return json({enabled:true,goals:[goal(sid)]});
    }
    if(/^\/api\/program\/goals\/.+\/history$/.test(p)){
      const sid=url.searchParams.get('session_id');const g=goal(sid);if(!['alpha','beta'].includes(sid)||!p.includes(g.id))f.bad.push({p,sid});
      return json({goal:g,plans:[{...g.plan,id:'p1',version_no:1,record_status:'confirmed',created_at:g.created_at,updated_at:g.updated_at}],cycles:[],activities:[],totals:{plans:1,cycles:0,activities:0},page:1,page_size:12});
    }
    if(/^\/api\/program\/(alpha|beta)$/.test(p))return json({enabled:true,runtime:{active_goal_id:goal(p.split('/')[3]).id},goals:[]});
    return original(input,options);
  };
}
exports.run=async function(browser,base='http://127.0.0.1:3229'){
 const c=await browser.newContext({viewport:{width:1440,height:900},serviceWorkers:'block'}),escaped=[],errors=[];
 await c.route('**/api/**',r=>{escaped.push(r.request().url());return r.fulfill({status:501,body:'{}'});});
 await c.addInitScript(installFixtures,{role:'admin'});await c.addInitScript(isolationFixtures);
 const p=await c.newPage();p.setDefaultTimeout(12000);p.on('pageerror',e=>errors.push(String(e)));
 try{
  await p.goto(base,{waitUntil:'domcontentloaded'});await p.getByText('first账户的alpha会话',{exact:true}).waitFor();
  for(const sid of ['alpha','beta']){
    if(sid==='beta')await p.getByRole('button',{name:/隔离会话beta/}).first().click();
    await p.getByText(`first账户的${sid}会话`,{exact:true}).waitFor();
    await p.getByRole('button',{name:'目标与记录',exact:true}).click();const d=p.getByRole('dialog',{name:'目标与记录',exact:true});
    await d.getByRole('button',{name:new RegExp(`first-${sid}-散步`)}).click();
    await d.getByRole('heading',{name:'PA 目标卡 · 第 1 版'}).waitFor();
    assert.equal(await d.getByText(`first-${sid==='alpha'?'beta':'alpha'}-散步`,{exact:true}).count(),0);
    await d.getByRole('button',{name:'关闭目标与记录'}).click();
  }
  await p.getByRole('button',{name:'目标与记录',exact:true}).click();const d=p.getByRole('dialog',{name:'目标与记录',exact:true});
  await d.getByRole('tab',{name:'每日记录',exact:true}).click();
  await d.getByRole('textbox',{name:'想再写一点（可留空）',exact:true}).fill('first账户不能泄漏的草稿');
  await p.evaluate(()=>{window.goalsIsolation.owner='second';window.dailyExperienceFixture.records=[];localStorage.setItem('psy-auth-token','second-token');window.dispatchEvent(new StorageEvent('storage',{key:'psy-auth-token',newValue:'second-token'}));});
  await p.getByText('second账户的alpha会话',{exact:true}).waitFor();assert.equal(await p.locator('dialog[open]').count(),0);
  await p.getByRole('button',{name:'目标与记录',exact:true}).click();
  await d.getByRole('button',{name:/second-alpha-散步/}).waitFor();assert.equal(await d.getByText(/first-(alpha|beta)-散步/).count(),0);
  await d.getByRole('tab',{name:'每日记录',exact:true}).click();
  assert.equal(await d.getByRole('textbox',{name:'想再写一点（可留空）',exact:true}).inputValue(),'');
  assert.deepEqual(await p.evaluate(()=>window.goalsIsolation.bad),[]);assert.deepEqual(escaped,[]);assert.deepEqual(errors,[]);
  const calls=await p.evaluate(()=>window.goalsIsolation.calls.filter(c=>c.path.includes('/goals/')||/^\/api\/program\/(alpha|beta)$/.test(c.path)));
  for(const sid of ['alpha','beta'])assert.ok(calls.some(c=>c.path.endsWith('/history')&&c.query.includes(`session_id=${sid}`)));
  return {passed:true,checks:['admin alpha/beta scoped overview+history','current goal state scoped by session','account storage switch hides previous dialog and clears draft'],calls};
 }finally{await c.close();}
};
if(require.main===module)(async()=>{const {chromium}=require('playwright');const b=await chromium.launch({headless:true,channel:'chrome'});try{console.log(JSON.stringify(await exports.run(b,process.argv[2]),null,2));}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
