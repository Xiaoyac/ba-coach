const assert = require('node:assert/strict');
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  const page = await browser.newPage({viewport:{width:390,height:844}});
  const errors=[]; page.on('pageerror', e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('psy-auth-token','synthetic-only'));
  let state={enabled:true,runtime:{current_module:'module_2',active_goal_id:null,active_cycle_id:null,row_version:0,flow_status:'active'},
    goals:[{id:'g1',title:'晚饭后散步',status:'active'}],draft:null,can_confirm:false,m1_reusable:true};
  await page.route('**/*', route=>{
    const url=new URL(route.request().url());
    if(url.hostname!=='127.0.0.1') return route.abort();
    const json=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    if(!url.pathname.startsWith('/api/')) return route.continue();
    if(url.pathname==='/api/auth/me') return json({username:'fixture',nickname:'测试用户',role:'user',profile_uuid:'test',email:'test@example.invalid',email_required:false,email_verified:true});
    if(url.pathname==='/api/conversations') return json(route.request().method()==='GET'?[]:{session_id:'test-chat',title:'新聊天',messages:[],next_module:'module_2',revision:1,updated_at:new Date().toISOString()});
    if(url.pathname.endsWith('/events')) return route.fulfill({contentType:'text/event-stream',body:''});
    if(url.pathname.endsWith('/revision')) return json({revision:1});
    if(url.pathname==='/api/conversations/test-chat') return json({session_id:'test-chat',title:'新聊天',messages:[],next_module:'module_2',revision:1,updated_at:new Date().toISOString()});
    if(url.pathname==='/api/program/test-chat/goal') {
      assert.equal(route.request().postDataJSON().goal_id,'g1');
      state={...state,runtime:{...state.runtime,active_goal_id:'g1',active_cycle_id:'c1',row_version:1},
        draft:{id:'r1',activity_content:'散步',schedule_text:'晚饭后',duration_minutes:10},record_hash:'a'.repeat(64),can_confirm:true};
      return json(state);
    }
    if(url.pathname==='/api/program/test-chat/confirm') {
      state={...state,runtime:{...state.runtime,current_module:'module_3'},draft:null,can_confirm:false};return json(state);
    }
    if(url.pathname==='/api/program/test-chat') return json(state);
    if(url.pathname==='/api/modules') return json({modules:['module_1','module_2','module_3','module_4']});
    return json({});
  });
  await page.goto('http://127.0.0.1:3120');
  await page.getByLabel('选择已有目标').selectOption('g1');
  await page.getByRole('button',{name:'继续此目标'}).click();
  await page.getByRole('heading',{name:'本次记录草稿'}).waitFor();
  assert.equal(await page.getByRole('button',{name:'确认这个计划'}).isDisabled(),true);
  await page.getByRole('checkbox').check();
  await page.screenshot({path:process.env.TEMP+'/bacoach-v2-program-mobile.png',fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
  await page.getByRole('button',{name:'确认这个计划'}).click();
  await page.getByText('我的目标 · 约定记录方式').waitFor();
  assert.deepEqual(errors,[]);
  await browser.close(); console.log('Mobile goal selection, confirmation guard, transition and overflow checks passed');
})().catch(e=>{console.error(e);process.exitCode=1});
