// Full production chat path: streamChat -> turn state -> MessageRow.
// All API calls are synthetic; public assets can be tested without touching accounts.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base=process.argv[2]||'http://127.0.0.1:3194';
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  for(const role of ['user','admin']){
   const context=await browser.newContext({viewport:{width:role==='user'?390:1280,height:900}});
   await context.addInitScript(({role})=>{
    localStorage.setItem('psy-auth-token','synthetic');
    const saved=JSON.parse(sessionStorage.getItem('lead-presentation-saved')||'null');
    const state={messages:saved||[{id:1,role:'assistant',content:'测试开场'}],revision:saved?2:1};
    const detail=()=>({session_id:'lead-test',title:'承接显示测试',reply_mode:'ack_deep',next_module:'module_1',updated_at:'2026-10-14T12:00:00Z',revision:state.revision,messages:state.messages,knowledge_mediator_node_enabled:true,reply_effort:'low'});
    const json=v=>new Response(JSON.stringify(v),{headers:{'Content-Type':'application/json'}});
    const original=window.fetch.bind(window);
    window.fetch=async(input,options={})=>{
     const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
     if(p==='/api/auth/me')return json({username:'synthetic',nickname:'测试',role,profile_uuid:'synthetic',email_required:false,email_verified:true});
     if(p==='/api/conversations')return json([detail()]);
     if(p==='/api/conversations/current'||p==='/api/conversations/lead-test')return json(detail());
     if(p.endsWith('/revision'))return json({revision:state.revision});
     if(p.endsWith('/events'))return new Response(new ReadableStream({start(){}}),{headers:{'Content-Type':'text/event-stream'}});
     if(p==='/api/chat/cancel')return json({cancelled:true});
     if(p==='/api/chat/stream'){
      const request=JSON.parse(options.body);
      return new Response(new ReadableStream({start(c){
       const emit=(event,payload)=>c.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`));
       emit('meta',{session_id:'lead-test',user_message_id:2,model:'synthetic'});
       const lead='听起来昨天的事情让你很疲惫，我们可以慢慢理清当时的感受。';
       emit('delta',{text:lead,phase:'lead'});
       emit('reply_wait',{waiting:true,generation_id:request.generation_id});
       window.finishLeadTest=()=>{
        emit('reply_wait',{waiting:false,generation_id:request.generation_id});
        emit('delta',{text:'\n\n',phase:'body'});
        emit('delta',{text:'我们先看看最让你觉得累的是哪一部分。',phase:'body'});
        state.messages.push({id:2,role:'user',content:request.message},{id:3,role:'assistant',reply_to_message_id:2,content:lead+'\n\n我们先看看最让你觉得累的是哪一部分。'});
        state.revision++;sessionStorage.setItem('lead-presentation-saved',JSON.stringify(state.messages));
        emit('done',{});emit('persisted',{saved:true});c.close();
       };
       options.signal?.addEventListener('abort',()=>{try{c.error(new DOMException('aborted','AbortError'));}catch{}});
      }}),{headers:{'Content-Type':'text/event-stream'}});
     }
     if(p.startsWith('/api/'))return json({});
     return original(input,options);
    };
   },{role});
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   // Prevent accidental access to non-fixture APIs even if the application changes.
   await page.route('**/api/**',route=>route.abort());
   await page.goto(base);await page.getByText('测试开场',{exact:true}).waitFor();
   await page.getByRole('textbox',{name:'Message',exact:true}).fill('昨天太累，没去散步。');
   await page.getByRole('button',{name:'Send',exact:true}).click();
   await page.waitForFunction(()=>typeof window.finishLeadTest==='function');
   await page.waitForTimeout(1800);
   const reply=page.locator('[data-streaming-reply]').last();
   const early=await reply.textContent();assert.ok(early.length>2&&early.length<15,role+': real delta handler must preserve lead phase');
   assert.equal(await page.locator('[data-reply-wait],[data-generation-status]').count(),0);
   await page.screenshot({path:`/tmp/lead-real-${role}.png`});
   await page.evaluate(()=>window.finishLeadTest());
   await page.waitForFunction(()=>document.querySelectorAll('[data-streaming-reply]')[1]?.textContent.includes('我们先看看'),{},{timeout:1500});
   await page.getByText('我们先看看最让你觉得累的是哪一部分。',{exact:true}).waitFor();
   await page.getByRole('button',{name:'停止生成',exact:true}).waitFor({state:'hidden'});
   assert.equal(await page.locator('[data-lead-cursor],[data-lead-status]').count(),0);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.reload();await page.getByText('我们先看看最让你觉得累的是哪一部分。',{exact:true}).waitFor();
   assert.equal(await page.locator('.reply-character').count(),0,'loaded history is not replayed');
   assert.deepEqual(errors,[]);await context.close();
   console.log('PASS: actual chat '+role+' SSE phase, slow lead, no dots, body handoff, persisted/reload, layout');
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
