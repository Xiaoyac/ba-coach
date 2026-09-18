// Synthetic streams only: no production account, database writes, or LLM calls.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const { chromium } = require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const url = process.env.CHAT_ISOLATION_QA_URL || 'http://127.0.0.1:3000';

(async () => {
  const browser = await chromium.launch({headless:true,channel:'chrome'});
  const out = path.resolve('.test-tmp/conversation-generation-isolation');
  await fs.mkdir(out,{recursive:true});
  try { for (const width of [1440,390]) {
    const context=await browser.newContext({viewport:{width,height:900}});
    await context.addInitScript(() => {
      localStorage.setItem('psy-auth-token','synthetic-isolation');
      const original=window.fetch.bind(window);
      const rooms={A:{session_id:'A',title:'房间A',revision:1,messages:[{id:1,role:'assistant',content:'这是房间A的历史'}]},
        B:{session_id:'B',title:'房间B',revision:1,messages:[{id:2,role:'assistant',content:'这是房间B的历史'}]}};
      const tasks={};
      const fixture=window.fixture={rooms,tasks,cancelled:[],holdFinal:null,releaseFinal:null,deferCancel:false,releaseCancel:null};
      const json=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
      const detail=id=>({...rooms[id],updated_at:'2026-09-17T12:00:00Z',next_module:'module_1'});
      window.emitTurn=(id,text)=>tasks[id].emit('delta',{text});
      window.finishTurn=(id,reply)=>{
        const t=tasks[id];
        rooms[id].messages.push({id:10+rooms[id].revision,role:'user',content:t.message},{id:11+rooms[id].revision,role:'assistant',content:reply});
        rooms[id].revision+=2;
        t.emit('persisted',{saved:true}); t.done=true;
      };
      window.fetch=async(input,options={})=>{
        const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
        if(p==='/api/auth/me')return json({username:'synthetic',nickname:'本地隔离验收',role:'user',profile_uuid:'synthetic',email_required:false,email_verified:true});
        if(p==='/api/conversations'&&options.method==='POST') {
          const id='C';rooms[id]={session_id:id,title:'房间C',revision:1,messages:[{role:'assistant',content:'这是房间C的历史'}]};return json(detail(id));
        }
        if(p==='/api/conversations')return json(Object.keys(rooms).map(detail));
        if(p.endsWith('/events'))return new Response(new ReadableStream({start(){},cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
        const match=p.match(/^\/api\/conversations\/([ABC])(?:\/(revision))?$/);
        if(match){
          const id=match[1];
          if(match[2])return json({revision:rooms[id].revision});
          if(fixture.holdFinal===id&&tasks[id]?.done){
            fixture.holdFinal=null;
            await new Promise(resolve=>fixture.releaseFinal=resolve);
          }
          return json(detail(id));
        }
        if(p==='/api/chat/stream'){
          const body=JSON.parse(options.body),id=body.session_id;
          return new Response(new ReadableStream({start(c){
            const emit=(event,data)=>c.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
            tasks[id]={id:body.generation_id,message:body.message,emit,done:false};
            emit('meta',{session_id:id,reply_module:'module_1'});
            options.signal?.addEventListener('abort',()=>{try{c.error(new DOMException('aborted','AbortError'));}catch{}});
          },cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
        }
        if(p==='/api/chat/cancel'){
          const {generation_id}=JSON.parse(options.body);
          fixture.cancelled.push(generation_id);
          if(fixture.deferCancel)await new Promise(resolve=>fixture.releaseCancel=resolve);
          return json({status:'cancelled'});
        }
        if(p.startsWith('/api/'))return json({});
        return original(input,options);
      };
    });
    const page=await context.newPage(),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(url);
    const log=()=>page.getByRole('log');
    const spinner=()=>page.locator('[data-generation-status]');
    const stop=()=>page.getByRole('button',{name:'停止生成',exact:true});
    const input=()=>page.getByRole('textbox',{name:'Message'});
    async function choose(id){
      if(width<768)await page.getByRole('button',{name:'切换对话侧栏'}).click();
      await page.getByRole('button',{name:new RegExp('房间'+id)}).click();
      await log().getByText('这是房间'+id+'的历史',{exact:true}).waitFor();
    }
    async function send(text){await input().fill(text);await page.getByRole('button',{name:'Send',exact:true}).click();await spinner().waitFor();}
    async function idle(){await page.waitForFunction(()=>!document.querySelector('[data-generation-status]'));}
    await log().getByText('这是房间A的历史',{exact:true}).waitFor();
    await send('A问题一');
    await choose('B');
    await page.waitForTimeout(600);
    await page.screenshot({path:path.join(out,`switch-B-${width}.png`)});
    assert.equal(await spinner().count(),0,'A的生成指示不应出现在B');
    assert.equal(await stop().count(),0,'B不应有停止A的按钮');
    assert.equal(await input().isEnabled(),true);
    await page.evaluate(()=>window.emitTurn('A','A流式片段'));
    assert.equal(await log().getByText('A流式片段',{exact:true}).count(),0);
    await send('B问题一');
    await page.evaluate(()=>{window.originalDateNow=Date.now;Date.now=()=>window.originalDateNow()+22000;});
    await choose('A');
    await log().getByText('A流式片段',{exact:true}).waitFor();
    assert.equal(await spinner().count(),1);
    assert.match(await spinner().innerText(),/已等待 \d+ 秒/,'返回聊天室不能把原任务计时清零');
    await page.evaluate(()=>{Date.now=window.originalDateNow;});
    await choose('B');
    await page.evaluate(()=>window.finishTurn('A','A最终回复'));
    await page.waitForTimeout(200);
    assert.equal(await spinner().count(),1,'A完成不能清除B的busy状态');
    assert.equal(await log().getByText('A最终回复',{exact:true}).count(),0);
    await page.evaluate(()=>{window.emitTurn('B','B最终回复');window.finishTurn('B','B最终回复');});
    await idle();
    await choose('A');await log().getByText('A最终回复',{exact:true}).waitFor();
    assert.equal(await log().getByText('B最终回复',{exact:true}).count(),0);
    // A's final transcript fetch returns after navigating away and B starts.
    await send('A问题二');
    await page.evaluate(()=>{window.fixture.holdFinal='A';window.finishTurn('A','A第二轮回复');});
    await page.waitForFunction(()=>!!window.fixture.releaseFinal);
    await choose('B');await send('B问题二');
    await page.evaluate(()=>window.fixture.releaseFinal());
    await page.waitForTimeout(200);
    assert.equal(await spinner().count(),1);
    assert.equal(await log().getByText('A第二轮回复',{exact:true}).count(),0);
    // Stopping B and switching to A must not stop A or show B's notices there.
    await choose('A');await send('A问题三');await choose('B');
    await page.evaluate(()=>window.fixture.deferCancel=true);
    await stop().click();
    await page.waitForFunction(()=>!!window.fixture.releaseCancel);
    await choose('A');
    await page.evaluate(()=>window.fixture.releaseCancel());
    await page.waitForTimeout(200);
    assert.equal(await spinner().count(),1);
    assert.equal(await stop().isEnabled(),true);
    assert.equal(await page.getByText(/已停止生成，未完成的回复未保存/).count(),0);
    const cancellation=await page.evaluate(()=>({ids:window.fixture.cancelled,B:window.fixture.tasks.B.id,A:window.fixture.tasks.A.id}));
    assert.deepEqual(cancellation.ids,[cancellation.B]);assert.notEqual(cancellation.B,cancellation.A);
    // Creating a room is navigation too: no borrowed spinner or deltas.
    if(width<768)await page.getByRole('button',{name:'切换对话侧栏'}).click();
    await page.getByRole('button',{name:'开启新对话',exact:true}).click();
    await log().getByText('这是房间C的历史',{exact:true}).waitFor();
    assert.equal(await spinner().count(),0);
    await page.evaluate(()=>{window.emitTurn('A','A第三轮回复');window.finishTurn('A','A第三轮回复');});
    await page.waitForTimeout(200);
    assert.equal(await log().getByText('A第三轮回复',{exact:true}).count(),0);
    await choose('A');await log().getByText('A第三轮回复',{exact:true}).waitFor();await idle();
    // A hidden room's failure must not become B's error or stop B's task.
    await send('A失败测试');await choose('B');await send('B继续测试');
    await page.evaluate(()=>{window.fixture.tasks.A.emit('error',{detail:'仅属于A的错误'});window.fixture.tasks.A.emit('persisted',{saved:false});});
    await page.waitForTimeout(200);
    assert.equal(await page.getByText(/仅属于A的错误/).count(),0);
    assert.equal(await spinner().count(),1);
    await page.evaluate(()=>{window.emitTurn('B','B继续成功');window.finishTurn('B','B继续成功');});
    await idle();await choose('A');
    assert.deepEqual(errors,[]);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(out,`final-A-${width}.png`)});
    console.log(JSON.stringify({width,isolatedBusy:true,concurrentRooms:true,returnToStream:true,timerPreserved:true,lateFinalFetch:true,cancelIsolation:true,newRoom:true,errorIsolation:true,errors}));
    await context.close();
  }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
