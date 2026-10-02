const assert = require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 const page=await browser.newPage({viewport:{width:1280,height:960}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let role='admin',saves=0, failSave=false; const efforts=[];
 const rooms={a:{session_id:'room-a',title:'中介开关测试A',revision:1,updated_at:'2026-10-02T12:00:00Z',messages:[{role:'assistant',content:'测试对话A'}],next_module:'module_1',routing_mode:'router_only',knowledge_mediator_enabled:true},b:{session_id:'room-b',title:'中介开关测试B',revision:1,updated_at:'2026-10-02T11:00:00Z',messages:[{role:'assistant',content:'测试对话B'}],next_module:'module_1',knowledge_mediator_enabled:true}};
 for(const room of Object.values(rooms))Object.assign(room,{reply_mode:'ack_deep',reply_effort:'low',reply_effort_options:['low','high','max']});
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
  if(u.pathname.endsWith('/reply-effort')){await new Promise(resolve=>setTimeout(resolve,200));if(failSave)return json({detail:'保存失败测试'},500); room.reply_effort=q.postDataJSON().effort;efforts.push(room.reply_effort);room.revision++;return json(room);}
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
  const toggle=page.getByRole('switch',{name:'是否使用新架构',exact:true});
  const slider=page.getByRole('slider',{name:'主回复思考强度'});
  const expectMode=async checked=>page.waitForFunction(value=>document.querySelector('[role="switch"]')?.getAttribute('aria-checked')===String(value),checked);
  const expectEffort=async label=>page.waitForFunction(value=>document.querySelector('input[type="range"]')?.getAttribute('aria-valuetext')===value&&!document.querySelector('input[type="range"]').disabled,label);
  await toggle.waitFor();await expectMode(false);
  await toggle.click();await expectMode(true);assert.equal(saves,1);assert.equal(rooms.a.knowledge_mediator_enabled,false);
  await slider.focus();await slider.press('ArrowRight');await expectEffort('high');assert.equal(efforts.at(-1),'high');
  await slider.press('End');await expectEffort('xhigh');assert.equal(efforts.at(-1),'max');
  await page.reload();await toggle.waitFor();await expectMode(true);await expectEffort('xhigh');
  await page.getByText('中介开关测试B',{exact:true}).click();await expectMode(false);await expectEffort('low');
  await page.getByText('中介开关测试A',{exact:true}).click();await expectMode(true);await expectEffort('xhigh');
  const box=await slider.boundingBox();await page.mouse.move(box.x+box.width*5/6,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/6,box.y+box.height/2,{steps:8});await page.mouse.up();await expectEffort('low');assert.equal(efforts.at(-1),'low');
  failSave=true;await slider.press('ArrowRight');await expectEffort('low');failSave=false;
  await page.getByRole('button',{name:'知识检索架构说明'}).click();await page.getByRole('note').getByText(/K3 重排/).waitFor();await page.keyboard.press('Escape');assert.equal(await page.getByRole('note').count(),0);
  await page.screenshot({path:'/tmp/controls-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);
  const bar=await page.getByTestId('conversation-controls').boundingBox();assert.ok(bar.height<=50,`toolbar height ${bar.height}`);
  await page.getByRole('button',{name:'思考强度说明'}).click();const help=await page.getByRole('note').boundingBox();assert.ok(help.x>=0&&help.x+help.width<=390);await page.keyboard.press('Escape');
  await page.screenshot({path:'/tmp/controls-mobile.png',fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await toggle.click();await expectMode(false);assert.equal(rooms.a.knowledge_mediator_enabled,true);
  const mobileBox=await slider.boundingBox();await slider.click({position:{x:mobileBox.width*5/6,y:mobileBox.height/2}});await expectEffort('xhigh');
  role='user';await page.reload();await page.getByText('测试对话A',{exact:true}).waitFor();
  assert.equal(await toggle.count(),0);assert.equal(await slider.count(),0);
  assert.deepEqual(errors,[]);console.log('PASS: switch mapping, effort low/high/max, keyboard, drag, failed save rollback, persistence, conversation isolation, help, one-row mobile fit, member hidden');
 } catch(error) {console.error((await page.locator('body').innerText()).slice(0,5000));await page.screenshot({path:'/tmp/hybrid-ui-failure.png',fullPage:true});throw error;
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
