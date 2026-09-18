// Synthetic browser acceptance; API calls are intercepted, including on the deployed build.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const baseUrl = process.env.REASONING_QA_URL || 'http://127.0.0.1:3000';

(async()=>{
  const out=path.resolve('.test-tmp',baseUrl.startsWith('https:')?'reasoning-split-0917-online':'reasoning-split-0917');await fs.mkdir(out,{recursive:true});
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {for(const width of [1440,390,360]){
    const context=await browser.newContext({viewport:{width,height:900},isMobile:width<600,hasTouch:width<600,permissions:['clipboard-read','clipboard-write']});
    await context.addInitScript(()=>{
      localStorage.setItem('psy-auth-token','synthetic-only');
      const original=window.fetch.bind(window);
      window.fixture={revision:1,messages:[{role:'user',content:'今天我们先聊聊。'},
        {role:'assistant',content:'可以，我们从今天的感受开始。',reasoning_content:'合成回复思考：先理解用户的感受，再回应。',routing_reasoning_content:'合成路由思考：目前保持模块一，不提前切换。',model_name:'reply-model-demo',router_model_name:'router-model-demo'}]};
      if(sessionStorage.getItem('reasoning-fixture'))window.fixture=JSON.parse(sessionStorage.getItem('reasoning-fixture'));
      else window.fixture.messages[1].timing={reply_thinking_ms:58814,reply_generation_ms:61614,router_processing_ms:17000};
      window.fetch=async (input,options={})=>{
        const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
        const f=window.fixture;
        const detail=()=>({session_id:'split-demo',title:'独立思考验收',next_module:'module_1',revision:f.revision,messages:f.messages,updated_at:'2026-09-17T03:00:00Z'});
        const json=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
        if(p==='/api/auth/me')return json({username:'synthetic',nickname:'本地验收',role:'user',profile_uuid:'synthetic',email_required:false,email_verified:true});
        if(p==='/api/conversations')return json([detail()]);
        if(p==='/api/conversations/split-demo')return json(detail());
        if(p.endsWith('/revision'))return json({revision:f.revision});
        if(p.endsWith('/events'))return new Response(new ReadableStream({start(){},cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
        if(p==='/api/program/split-demo')return json({enabled:false});
        if(p==='/api/chat/stream'){
          const user=JSON.parse(options.body).message;
          return new Response(new ReadableStream({start(c){
            const emit=(event,data)=>c.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
            const text='这是本地模拟的新回复。', reasoning='新回复的独立思考。';
            emit('meta',{session_id:'split-demo',model:'reply-model-demo',reply_module:'module_1',routing_pending:false});
            emit('delta',{text});emit('reasoning_delta',{text:reasoning});
            window.markRouting=()=>emit('meta',{routing_pending:true});
            window.finishRouting=()=>{
              f.messages=[...f.messages,{role:'user',content:user},{role:'assistant',content:text,reasoning_content:reasoning,routing_reasoning_content:'新路由结果已返回。',model_name:'reply-model-demo',router_model_name:'router-model-demo'}];
              f.revision+=2;emit('done',{});emit('persisted',{saved:true});c.close();
            };
          },cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
        }
        if(p.startsWith('/api/'))return json({});
        return original(input,options);
      };
    });
    const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const shot=async name=>{await page.waitForTimeout(650);await page.screenshot({path:path.join(out,`${name}-${width}.png`),scale:'css'});};
    await page.goto(baseUrl);
    const replyButton=()=>page.getByRole('button',{name:/^(查看|收起)回复深度思考/}).last();
    const routeButton=()=>page.getByRole('button',{name:/^(查看|收起)路由深度思考/}).last();
    const replyPanel=()=>page.getByRole('region',{name:/收起回复深度思考/});
    const routePanel=()=>page.getByRole('region',{name:/收起路由深度思考/});
    const debug=()=>page.getByRole('button',{name:'调试详情',exact:true}).last();
    await debug().waitFor();await debug().click();
    await replyButton().waitFor();
    assert.equal(await replyButton().getAttribute('aria-expanded'),'false');
    await shot('collapsed');
    await replyButton().click();
    assert.match(await replyPanel().innerText(),/合成回复思考/);
    assert.match(await replyPanel().innerText(),/思考阶段耗时（估算）：58.8 秒/);
    assert.match(await replyPanel().innerText(),/回复总耗时：61.6 秒/);
    assert.doesNotMatch(await replyPanel().innerText(),/合成路由思考|router-model-demo/);
    await shot('reply');
    await routeButton().click();
    assert.equal(await replyPanel().count(),0);
    assert.match(await routePanel().innerText(),/合成路由思考/);
    assert.match(await routePanel().innerText(),/路由处理耗时：17.0 秒/);
    assert.doesNotMatch(await routePanel().innerText(),/58.8 秒|61.6 秒/);
    assert.doesNotMatch(await routePanel().innerText(),/合成回复思考|reply-model-demo/);
    await shot('router');
    // Keyboard activation closes and reopens the selected disclosure.
    await routeButton().focus();await page.keyboard.press('Enter');
    assert.equal(await routePanel().count(),0);
    await page.keyboard.press('Space');assert.equal(await routePanel().count(),1);
    await page.getByRole('button',{name:'复制消息',exact:true}).last().click();
    assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'可以，我们从今天的感受开始。');
    // Missing-channel placeholders do not borrow content from the other model.
    await page.evaluate(()=>{window.fixture.messages[1].reasoning_content='';window.fixture.revision++;window.dispatchEvent(new Event('focus'));});
    await replyButton().click();
    await page.getByText('本轮回复模型未提供独立的思考内容。',{exact:true}).waitFor();
    await page.evaluate(()=>{window.fixture.messages[1].reasoning_content='长内容测试 '+('long-unbroken-token'.repeat(70))+'\n<script>window.injected=true</script>';window.fixture.messages[1].routing_reasoning_content='';window.fixture.revision++;window.dispatchEvent(new Event('focus'));});
    await routeButton().click();
    await page.getByText('本轮尚无可查看的路由思考记录。',{exact:true}).waitFor();
    await replyButton().click();await page.getByText(/长内容测试/).waitFor();
    assert.equal(await page.evaluate(()=>window.injected),undefined);
    // Explore long text, theme roundtrip and boundaries on each viewport.
    if(width<600)await page.getByRole('button',{name:'切换对话侧栏'}).click();
    await page.getByRole('button',{name:'切换到暗色主题'}).click();
    if(width<600)await page.keyboard.press('Escape');
    await shot('long-dark');
    const fit=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    assert.ok(fit.scroll<=fit.width);
    if(width<600)await page.getByRole('button',{name:'切换对话侧栏'}).click();
    await page.getByRole('button',{name:'切换到暖色主题'}).click();
    if(width<600)await page.keyboard.press('Escape');
    await replyButton().click();
    // Live input: only the router panel reports the router's pending state.
    await page.getByRole('textbox',{name:'Message'}).fill('本地模拟下一轮');
    await page.getByRole('button',{name:'Send',exact:true}).click();
    await page.getByText('这是本地模拟的新回复。',{exact:true}).waitFor();
    await debug().click();
    await routeButton().click();await page.getByText('回复完成后才会进行路由判断。',{exact:true}).waitFor();
    await page.evaluate(()=>window.markRouting());
    await page.getByText('回复已完成，路由正在后台判断，请稍候。',{exact:true}).waitFor();
    assert.match(await routePanel().innerText(),/路由处理耗时：记录中/);
    await shot('pending');
    await page.evaluate(()=>window.finishRouting());
    await page.getByText('新路由结果已返回。',{exact:true}).waitFor();
    assert.doesNotMatch(await routePanel().innerText(),/新回复的独立思考/);
    assert.match(await routePanel().innerText(),/路由处理耗时：未记录/);
    await page.evaluate(()=>sessionStorage.setItem('reasoning-fixture',JSON.stringify(window.fixture)));
    await page.reload();await debug().waitFor();await debug().click();await replyButton().waitFor();
    assert.equal(await replyButton().getAttribute('aria-expanded'),'false');
    await routeButton().click();await page.getByText('新路由结果已返回。',{exact:true}).waitFor();
    await page.evaluate(()=>{window.fixture.messages.forEach(m=>{m.reasoning_content='';m.routing_reasoning_content='';});window.fixture.revision++;window.dispatchEvent(new Event('focus'));});
    await page.getByText('本轮尚无可查看的路由思考记录。',{exact:true}).waitFor();
    await debug().click();assert.equal(await routeButton().count(),0);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({width,fit,independentPanels:true,keyboard:true,copyBodyOnly:true,missingChannels:true,liveRouting:true,reload:true,errors}));
    await context.close();
  }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
