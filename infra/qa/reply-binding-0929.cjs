// Isolated stale-client/repeated-input regression. Never contacts real APIs.
const assert=require('node:assert/strict');
exports.run=async function(browser,baseUrl='http://127.0.0.1:3016') {
 const results=[];
 for(const width of [1440,390]) for(const delayedMeta of [false,true]) {
  const context=await browser.newContext({viewport:{width,height:900}});
  await context.addInitScript(({delayedMeta})=>{
   localStorage.setItem('psy-auth-token','synthetic');
   const fixture=window.fixture={aborted:0,delayedMeta,revision:1,messages:[{id:1,role:'assistant',content:'测试开场'}]};
   const detail=()=>({session_id:'test',title:'错位测试',next_module:'module_3',updated_at:'2026-09-29T01:00:00Z',revision:fixture.revision,messages:fixture.messages});
   const json=v=>new Response(JSON.stringify(v),{headers:{'Content-Type':'application/json'}});
   let eventController,mainController;
   const emit=(c,event,payload)=>c?.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`));
   window.sendMeta=()=>emit(mainController,'meta',{session_id:'test',user_message_id:4,model:'synthetic'});
   window.publish=()=>{fixture.revision++;emit(eventController,'snapshot',detail());};
   window.finish=()=>{fixture.messages.push({id:5,role:'assistant',reply_to_message_id:4,content:'这才是本轮回复'});window.publish();};
   const original=window.fetch.bind(window);
   window.fetch=async(input,options={})=>{
    const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
    if(p==='/api/auth/me')return json({username:'synthetic',nickname:'测试',role:'user',profile_uuid:'synthetic',email_required:false,email_verified:true});
    if(p==='/api/conversations')return json([detail()]);
    if(p==='/api/conversations/current'||p==='/api/conversations/test')return json(detail());
    if(p.endsWith('/revision'))return json({revision:fixture.revision});
    if(p.endsWith('/events'))return new Response(new ReadableStream({start(c){eventController=c;},cancel(){eventController=null;}}),{headers:{'Content-Type':'text/event-stream'}});
    if(p==='/api/chat/stream'){
     fixture.messages.push({id:2,role:'user',content:'好的'},{id:3,role:'assistant',content:'旧回复',reply_to_message_id:2},{id:4,role:'user',content:'好的'});
     return new Response(new ReadableStream({start(c){mainController=c;if(!delayedMeta)window.sendMeta();
      options.signal?.addEventListener('abort',()=>{fixture.aborted++;try{c.error(new DOMException('aborted','AbortError'));}catch{}});
     },cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
    }
    if(p.startsWith('/api/'))return json({});
    return original(input,options);
   };
  },{delayedMeta});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(baseUrl);await page.getByText('测试开场',{exact:true}).waitFor();
  await page.getByRole('textbox',{name:'Message',exact:true}).fill('好的');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await page.getByRole('button',{name:'停止生成',exact:true}).waitFor();
  await page.waitForFunction(()=>window.fixture.messages.length===4);
  await page.evaluate(()=>window.publish());
  // Cover event sync and the independent 5-second recovery poll.
  await page.waitForTimeout(5300);
  assert.equal(await page.evaluate(()=>window.fixture.aborted),0);
  assert.equal(await page.getByRole('button',{name:'停止生成',exact:true}).count(),1);
  assert.equal(await page.getByText('旧回复',{exact:true}).count(),0);
  await page.evaluate(()=>window.finish());
  if(delayedMeta){
   await page.waitForTimeout(300); assert.equal(await page.evaluate(()=>window.fixture.aborted),0);
   await page.evaluate(()=>window.sendMeta());await page.waitForTimeout(100);await page.evaluate(()=>window.publish());
  }
  await page.getByText('这才是本轮回复',{exact:true}).waitFor();
  await page.getByRole('button',{name:'停止生成',exact:true}).waitFor({state:'hidden'});
  assert.equal(await page.evaluate(()=>window.fixture.aborted),1);assert.deepEqual(errors,[]);
  results.push({width,delayedMeta,passed:true});await context.close();
 }
 return results;
};
