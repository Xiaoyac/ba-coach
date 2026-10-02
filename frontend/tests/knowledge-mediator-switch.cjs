const assert = require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 const page=await browser.newPage({viewport:{width:1280,height:960}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let role='admin',saves=0;
 const rooms={a:{session_id:'room-a',title:'中介开关测试A',revision:1,updated_at:'2026-10-02T12:00:00Z',messages:[{role:'assistant',content:'测试对话A'}],next_module:'module_1',routing_mode:'router_only',knowledge_mediator_enabled:true},b:{session_id:'room-b',title:'中介开关测试B',revision:1,updated_at:'2026-10-02T11:00:00Z',messages:[{role:'assistant',content:'测试对话B'}],next_module:'module_1',knowledge_mediator_enabled:true}};
 await page.addInitScript(()=>localStorage.setItem('psy-auth-token','synthetic-mediator-test'));
 await page.route('**/*',async route=>{
  const q=route.request(),u=new URL(q.url());
  if(u.hostname!=='127.0.0.1')return route.abort();
  if(!u.pathname.startsWith('/api/'))return route.continue();
  const json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  const room=u.pathname.includes('room-b')?rooms.b:rooms.a;
  if(u.pathname==='/api/auth/me')return json({username:'fixture',nickname:'测试用户',role,profile_uuid:'synthetic',email_required:false,email_verified:true});
  if(u.pathname==='/api/conversations')return json(Object.values(rooms));
  if(u.pathname==='/api/conversations/current')return json(rooms.a);
  if(u.pathname.endsWith('/knowledge-mediator')){room.knowledge_mediator_enabled=q.postDataJSON().enabled;room.revision++;saves++;return json(room);}
  if(u.pathname.endsWith('/events'))return route.fulfill({contentType:'text/event-stream',body:''});
  if(u.pathname.endsWith('/revision'))return json({revision:room.revision});
  if(u.pathname.startsWith('/api/conversations/'))return json(room);
  if(u.pathname==='/api/modules')return json({modules:['module_1','module_2','module_3','module_4']});
  return json({});
 });
 try{
  await page.goto(process.env.MEDIATOR_TEST_URL || 'http://127.0.0.1:3117');
  await page.getByText('中介开关测试A',{exact:true}).click();
  const toggle=page.getByRole('switch',{name:'知识中介',exact:true});
  await toggle.waitFor();assert.equal(await toggle.getAttribute('aria-checked'),'true');
  await toggle.click();await page.waitForFunction(()=>document.querySelector('[aria-label="知识中介"]')?.getAttribute('aria-checked')==='false');
  assert.equal(saves,1);
  await page.reload();await toggle.waitFor();assert.equal(await toggle.getAttribute('aria-checked'),'false');
  await page.getByText('中介开关测试B',{exact:true}).click();await page.waitForFunction(()=>document.querySelector('[aria-label="知识中介"]')?.getAttribute('aria-checked')==='true');
  await page.getByText('中介开关测试A',{exact:true}).click();await page.waitForFunction(()=>document.querySelector('[aria-label="知识中介"]')?.getAttribute('aria-checked')==='false');
  await page.screenshot({path:'mediator-switch-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'mediator-switch-mobile.png',fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await toggle.click();await page.waitForFunction(()=>document.querySelector('[aria-label="知识中介"]')?.getAttribute('aria-checked')==='true');
  role='user';await page.reload();await page.getByText('测试对话A',{exact:true}).waitFor();
  assert.equal(await page.getByRole('switch',{name:'知识中介',exact:true}).count(),0);
  assert.deepEqual(errors,[]);console.log('PASS: toggle, reload persistence, conversation isolation, restore, mobile fit, member hidden');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
