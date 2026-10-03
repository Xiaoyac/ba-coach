// Synthetic SSE browser acceptance; all application API calls are intercepted.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
exports.run=async function(browser,baseUrl='http://127.0.0.1:3027') {
 const results=[];
 for(const width of [1440,390]) {
  const context=await browser.newContext({viewport:{width,height:900}});
  await context.addInitScript(()=>{
   localStorage.setItem('psy-auth-token','synthetic');
   const fixture=window.fixture=JSON.parse(sessionStorage.getItem('lead-fixture')||'null')||{revision:1,messages:[{id:1,role:'assistant',content:'测试开场'}]};
   const detail=()=>({session_id:'lead-test',title:'接话衔接测试',reply_mode:'ack_deep',next_module:'module_1',updated_at:'2026-10-03T01:00:00Z',revision:fixture.revision,messages:fixture.messages});
   const json=v=>new Response(JSON.stringify(v),{headers:{'Content-Type':'application/json'}});
   const original=window.fetch.bind(window);
   window.fetch=async(input,options={})=>{
    const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
    if(p==='/api/auth/me')return json({username:'synthetic',nickname:'测试',role:'user',profile_uuid:'synthetic',email_required:false,email_verified:true});
    if(p==='/api/conversations')return json([detail()]);
    if(p==='/api/conversations/current'||p==='/api/conversations/lead-test')return json(detail());
    if(p.endsWith('/revision'))return json({revision:fixture.revision});
    if(p.endsWith('/events'))return new Response(new ReadableStream({start(){},cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
    if(p==='/api/chat/stream'){
     const request=JSON.parse(options.body);fixture.request=request;
     return new Response(new ReadableStream({start(c){
      const emit=(event,payload)=>c.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`));
      emit('meta',{session_id:'lead-test',user_message_id:2,model:'synthetic'});
      emit('delta',{text:'听',phase:'lead'});
      emit('reply_wait',{waiting:true,generation_id:request.generation_id});
      emit('reasoning_delta',{text:'正在生成后续正文'});
      window.nextLead=()=>emit('delta',{text:'起来',phase:'lead'});
      window.finishBody=()=>{
       emit('delta',{text:'昨天确实很累。',phase:'lead'});
       emit('reply_wait',{waiting:false,generation_id:request.generation_id});
       emit('delta',{text:'\n\n',phase:'body'});
       emit('delta',{text:'可以从当时遇到的困难说起。',phase:'body'});
       fixture.messages.push({id:2,role:'user',content:request.message},{id:3,role:'assistant',reply_to_message_id:2,content:'听起来昨天确实很累。\n\n可以从当时遇到的困难说起。'});
       fixture.revision++;sessionStorage.setItem('lead-fixture',JSON.stringify(fixture));
       emit('done',{});emit('persisted',{saved:true});c.close();
      };
      options.signal?.addEventListener('abort',()=>{try{c.error(new DOMException('aborted','AbortError'));}catch{}});
     },cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
    }
    if(p.startsWith('/api/'))return json({});
    return original(input,options);
   };
  });
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(baseUrl);await page.getByText('测试开场',{exact:true}).waitFor();
  await page.getByRole('textbox',{name:'Message',exact:true}).fill('昨天太累，没去散步。');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await page.getByText('听',{exact:true}).waitFor();
  assert.equal(await page.getByText('听起来昨天确实很累。',{exact:true}).count(),0);
  await page.evaluate(()=>window.nextLead());
  await page.getByText('听起来',{exact:true}).waitFor();
  await page.evaluate(()=>window.finishBody());
  await page.getByText('听起来昨天确实很累。',{exact:true}).waitFor();
  await page.getByText('可以从当时遇到的困难说起。',{exact:true}).waitFor();
  await page.getByRole('button',{name:'停止生成',exact:true}).waitFor({state:'hidden'});
  assert.deepEqual(errors,[]);
  const content=await page.locator('main').innerText();
  assert.ok(content.indexOf('听起来昨天确实很累。')<content.indexOf('可以从当时遇到的困难说起。'));
  await fs.mkdir('.test-tmp/lead-continuation',{recursive:true});
  await page.screenshot({path:`.test-tmp/lead-continuation/${width}.png`});
  await page.reload();
  await page.getByText('听起来昨天确实很累。',{exact:true}).waitFor();
  assert.equal(await page.getByText('听起来昨天确实很累。',{exact:true}).count(),1);
  assert.equal(await page.getByText('可以从当时遇到的困难说起。',{exact:true}).count(),1);
  results.push({width,passed:true,progressive:true,ordered:true,reload:true});await context.close();
 }
 return results;
};
if(require.main===module){
 const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
 (async()=>{const browser=await chromium.launch({headless:true,channel:'chrome'});try{console.log(await exports.run(browser));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
}
