const assert = require('node:assert/strict');
const {chromium} = require('playwright');
(async () => {
  const browser = await chromium.launch({headless:true,channel:'msedge'});
  const page = await browser.newPage({viewport:{width:390,height:844}});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(() => {
    localStorage.setItem('psy-auth-token','synthetic');
    const original=window.fetch.bind(window);
    window.fixture={revision:1,messages:[{role:'assistant',content:'欢迎'}],mode:'normal',expired:false};
    window.fetch=async (input, options={})=>{
      const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
      const f=window.fixture;
      const detail=()=>({session_id:'fixture',title:'测试',updated_at:new Date().toISOString(),revision:f.revision,messages:f.messages,next_module:'module_1'});
      const json=(data,status=200)=>Promise.resolve(new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}}));
      if(p==='/api/auth/me')return json({username:'fixture',nickname:'被试',role:'user',profile_uuid:'fixture',email_required:false,email_verified:true});
      if(p==='/api/conversations')return json([{...detail(),messages:undefined}]);
      if(p.endsWith('/revision'))return json({revision:f.revision});
      if(p==='/api/conversations/fixture')return json(detail());
      if(p.endsWith('/events'))return new Response(new ReadableStream({start(c){window.snapshotController=c},cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
      if(p==='/api/chat/stream'){
        const body=JSON.parse(options.body), reply='## 建议\n**先试五分钟**\n- 散步\n<script>window.hacked=true</script>';
        return new Response(new ReadableStream({start(c){
          const emit=(event,data)=>c.enqueue(new TextEncoder().encode(`event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`));
          emit('meta',{session_id:'fixture'});
          const timer=setTimeout(()=>{
            f.messages=[...f.messages,{role:'user',content:body.message},{role:'assistant',content:reply}];f.revision+=2;
            if(f.mode==='normal'){emit('delta',{text:reply});emit('done',{});emit('persisted',{saved:true});}
          },100);
          options.signal?.addEventListener('abort',()=>{clearTimeout(timer);try{c.error(new DOMException('aborted','AbortError'))}catch{}});
        },cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
      }
      if(p==='/api/assessment/history')return json({items:[],has_more:false,next_offset:null});
      if(p==='/api/profile')return json({detail:'Not authenticated'},401);
      if(p.startsWith('/api/'))return json({});
      return original(input,options);
    };
  });
  try {
    await page.goto(process.env.CHAT_RELIABILITY_QA_URL || 'http://127.0.0.1:3108');
    const input=page.getByPlaceholder('慢慢说…');
    await input.fill('第一次'); await input.press('Enter');
    await page.locator('strong').filter({hasText:'先试五分钟'}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('textarea').disabled);
    assert.equal(await page.evaluate(()=>window.hacked),undefined);
    await page.evaluate(()=>window.snapshotController.enqueue(new TextEncoder().encode('event: snapshot\ndata: '+JSON.stringify({session_id:'fixture',title:'旧',updated_at:'2026-01-01',revision:1,messages:[{role:'assistant',content:'旧消息'}],next_module:'module_1'})+'\n\n')));
    await page.waitForTimeout(300);
    assert.equal(await page.locator('strong').filter({hasText:'先试五分钟'}).count(),1);
    await page.evaluate(()=>window.fixture.mode='stalled');
    await input.fill('第二次');await input.press('Enter');
    await page.waitForFunction(()=>document.querySelectorAll('strong').length===2,{},{timeout:15000});
    await page.waitForFunction(()=>!document.querySelector('textarea').disabled);
    // Mobile drawer entry is available to a non-admin account.
    await page.getByRole('button',{name:'切换对话侧栏',exact:true}).click();
    await page.getByRole('button',{name:'记录今日',exact:true}).click();
    await page.getByRole('button',{name:'查看历史',exact:true}).click();
    await page.getByRole('heading',{name:'历史每日记录'}).waitFor();
    await page.getByText('还没有历史记录').waitFor();
    await page.getByRole('button',{name:'关闭',exact:true}).click();
    await page.getByRole('button',{name:'账号菜单（当前账号：被试）'}).click();
    await page.getByRole('menuitem',{name:'我的档案'}).click();
    await page.getByRole('alert').filter({hasText:'登录已失效'}).waitFor();
    assert.equal(await page.evaluate(()=>localStorage.getItem('psy-auth-token')),null);
    assert.deepEqual(errors,[]);
    console.log('PASS: durable SSE completion without EOF, stalled-stream recovery, stale revision rejection, safe Markdown, subject history entry, expired-session UI');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
