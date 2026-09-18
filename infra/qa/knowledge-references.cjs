// Synthetic, intercepted API acceptance. Does not create accounts or call LLMs.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const baseUrl = process.env.CHUNKS_QA_URL || 'http://127.0.0.1:3000';

(async () => {
  const out = path.resolve('.test-tmp', baseUrl.startsWith('https:') ? 'knowledge-references-online' : 'knowledge-references');
  await fs.mkdir(out, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  try { for (const width of [1440, 390, 360]) {
    const context = await browser.newContext({viewport:{width,height:900},isMobile:width<600,hasTouch:width<600});
    await context.addInitScript(() => {
      localStorage.setItem('psy-auth-token','synthetic-only');
      const original = window.fetch.bind(window);
      window.fixture = {revision:1, calls:0, fail:false, messages:[{id:10,role:'assistant',content:'可以先从短时间的散步开始，留意自己的感受。',reasoning_content:'回复独立思考',routing_reasoning_content:'路由独立思考'}],
        snapshot:{available:true,module:'module_2',retrieval_outcome:'returned',gate_reason:'knowledge_question',mediator_status:'completed',mediator_reason:'guided',context_withheld:false,validator_status:'passed',mediator_reasoning_content:'中介独立思考：合成验收内容，不是回复思考或使用建议。',mediator_model:'mediator-demo',mediator_duration_ms:820,
          recalled:[{id:'BA-001',source:'行为激活 · 活动安排',score:.8274,text:'把活动拆分成可尝试的小步骤，结合自己的体力和偏好。\n<script>window.injected=true</script>'},{id:'PA-002',source:'活动记录',score:.4321,text:'记录活动后情绪的变化。'}],
          provided:[{id:'BA-001',source:'行为激活 · 活动安排',score:.8274,text:'把活动拆分成可尝试的小步骤，结合自己的体力和偏好。\n<script>window.injected=true</script>'}]}};
      window.fetch = async (input, options={}) => {
        const p = new URL(typeof input==='string'?input:input.url, location.href).pathname;
        const f=window.fixture;
        const json = (data,status=200) => new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
        const detail=()=>({session_id:'chunks-demo',title:'知识片段调试验收',next_module:'module_2',revision:f.revision,messages:f.messages,updated_at:'2026-09-17T03:00:00Z'});
        if(p==='/api/auth/me')return json({username:'synthetic',nickname:'本地验收',role:'admin',profile_uuid:'synthetic',email_required:false,email_verified:true});
        if(p==='/api/conversations')return json([detail()]);
        if(p==='/api/conversations/chunks-demo')return json(detail());
        if(p.endsWith('/revision'))return json({revision:f.revision});
        if(p.endsWith('/events'))return new Response(new ReadableStream({start(){},cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
        if(p.endsWith('/knowledge')){f.calls++;return json(f.snapshot,f.fail?503:200);}
        if(p==='/api/chat/stream')return new Response(new ReadableStream({start(c){
          const emit=(event,data)=>c.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
          const content='这是合成的新一轮回复。';
          emit('meta',{session_id:'chunks-demo',reply_module:'module_2'});emit('delta',{text:content});
          window.finishTurn=()=>{f.messages.push({id:11,role:'user',content:JSON.parse(options.body).message},{id:12,role:'assistant',content});f.revision++;emit('persisted',{saved:true});c.close();};
        },cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
        if(p.startsWith('/api/program/'))return json({enabled:false});
        if(p.startsWith('/api/'))return json({});
        return original(input,options);
      };
    });
    const page=await context.newPage(); const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(baseUrl);
    const accountMenu = page.getByRole('button', {name:/账号菜单/});
    await accountMenu.click();
    await page.getByRole('menu').waitFor();
    assert.equal(await page.getByRole('menuitem', {name:'本次记录与确认', exact:true}).count(), 0);
    await page.screenshot({path:path.join(out,`account-menu-dialogue-first-${width}.png`),scale:'css'});
    await accountMenu.click();
    const button=()=>page.getByRole('button',{name:'对话参考 chunk',exact:true}).last();
    const panel=()=>page.getByRole('region',{name:'对话参考 chunk',exact:true});
    const debug=()=>page.getByRole('button',{name:'调试详情',exact:true}).last();
    const mediator=()=>page.getByRole('button',{name:'中介节点深度思考',exact:true}).last();
    const mediatorPanel=()=>page.getByRole('region',{name:'中介节点深度思考',exact:true});
    await debug().waitFor();assert.equal(await button().count(),0);
    await page.waitForTimeout(650);await page.screenshot({path:path.join(out,`collapsed-${width}.png`),scale:'css'});
    await debug().click();
    await button().waitFor(); assert.equal(await page.evaluate(()=>window.fixture.calls),0);
    await button().focus();await page.keyboard.press('Enter');
    await page.getByText('本轮召回 2 段 · 传给回复模型 1 段',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>window.injected),undefined);
    assert.equal(await panel().getByText('ID：BA-001 · 检索分数（类型未记录）：0.8274',{exact:true}).count(),2);
    assert.equal(await panel().getByText('ID：PA-002 · 检索分数（类型未记录）：0.4321',{exact:true}).count(),1);
    await page.waitForTimeout(350);await page.screenshot({path:path.join(out,`chunks-light-${width}.png`),scale:'css'});
    for (const [score_type,score,expected] of [
      ['model_selection',null,'模型筛选 · 未计算数值分数'],
      [null,0,'评分方式未记录，暂不展示数值'],
      [null,null,'数值分数未记录'],
      ['lexical',0,'词法检索分数：0.0000'],
      ['bm25f',.8274,'BM25F 排序分数：0.8274'],
      ['cross_encoder',-.5,'重排模型分数：-0.5000'],
      ['rrf',.03,'RRF 融合分数：0.0300'],
    ]) {
      await button().click();
      await page.evaluate(({score_type,score})=>{
        for (const group of ['recalled','provided']) Object.assign(window.fixture.snapshot[group][0],{score_type,score});
      },{score_type,score});
      await button().click();
      await panel().getByText(`ID：BA-001 · ${expected}`,{exact:true}).first().waitFor();
      assert.equal(await panel().getByText(`ID：BA-001 · ${expected}`,{exact:true}).count(),2);
      if(score_type==='model_selection') {
        assert.ok(!(await panel().innerText()).includes('0.0000'));
        await page.screenshot({path:path.join(out,`model-selection-${width}.png`),scale:'css'});
      }
    }
    await page.getByRole('button',{name:'查看回复深度思考',exact:true}).click();assert.equal(await panel().count(),0);
    await page.getByText('回复独立思考',{exact:true}).waitFor();
    await page.getByRole('button',{name:'查看路由深度思考',exact:true}).click();await page.getByText('路由独立思考',{exact:true}).waitFor();
    await mediator().click();await mediatorPanel().getByText('中介独立思考：合成验收内容，不是回复思考或使用建议。',{exact:true}).waitFor();
    assert.match(await mediatorPanel().innerText(),/中介处理总耗时：0.8 秒/);
    assert.doesNotMatch(await mediatorPanel().innerText(),/路由独立思考|回复独立思考/);
    await page.waitForTimeout(350);await page.screenshot({path:path.join(out,`mediator-${width}.png`),scale:'css'});
    await debug().click();assert.equal(await mediatorPanel().count(),0);assert.equal(await button().count(),0);await debug().click();
    await button().click(); await panel().getByText('1. 行为激活 · 活动安排',{exact:true}).first().click();
    // Failure + retry must not report an empty successful retrieval.
    await button().click();await page.evaluate(()=>window.fixture.fail=true);await button().click();
    await page.getByRole('button',{name:'重试加载'}).waitFor();await page.evaluate(()=>window.fixture.fail=false);
    await page.getByRole('button',{name:'重试加载'}).click();await page.getByText(/本轮召回 2 段/).waitFor();
    await button().click();await page.evaluate(()=>{window.fixture.snapshot.provided=[];window.fixture.snapshot.mediator_status='fallback';window.fixture.snapshot.mediator_reason='timeout';});
    await button().click();await page.getByText('本轮召回 2 段 · 传给回复模型 0 段',{exact:true}).waitFor();
    await page.getByText(/中介超时，片段未放行/).waitFor();
    for (const [reason, text] of [
      ['output_truncated','中介输出被截断，片段未放行'],
      ['invalid_json','中介结果不是有效 JSON，片段未放行'],
      ['invalid_schema','中介结果字段不符合契约，片段未放行'],
      ['empty_output','中介没有返回有效结果，片段未放行'],
      ['invalid_evidence','中介引用校验失败，片段未放行'],
      ['context_too_large','上下文超过安全处理上限，片段未放行'],
    ]) {
      await button().click();await page.evaluate(value=>window.fixture.snapshot.mediator_reason=value,reason);
      await button().click();await panel().getByText(new RegExp(text)).waitFor();
    }
    await button().click();await page.evaluate(()=>window.fixture.snapshot.mediator_reason='timeout');await button().click();
    await panel().getByText(/中介超时，片段未放行/).waitFor();
    // Long text, dark theme and viewport overflow.
    await button().click();await page.evaluate(()=>window.fixture.snapshot.recalled[0].text='长片段 '+('unbroken-token'.repeat(300)));
    if(width<600)await page.getByRole('button',{name:'切换对话侧栏'}).click();
    await page.getByRole('button',{name:'切换到暗色主题'}).click();if(width<600)await page.keyboard.press('Escape');
    await button().click();await page.getByText(/长片段/).waitFor();
    const fit=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(fit.scroll<=fit.width);
    assert.ok(await panel().evaluate(el=>el.scrollWidth<=el.clientWidth+1));
    await page.waitForTimeout(350);await page.screenshot({path:path.join(out,`chunks-dark-${width}.png`),scale:'css'});
    await button().click();await page.evaluate(()=>window.fixture.snapshot.available=false);await button().click();
    await page.getByText(/本条回复未记录参考片段/).waitFor();
    await page.reload();await debug().waitFor();await debug().click();await button().waitFor();assert.equal(await button().getAttribute('aria-expanded'),'false');
    await button().click();await page.getByText(/本轮召回 2 段/).waitFor();
    await button().click();await page.evaluate(()=>{window.fixture.snapshot.recalled=[];window.fixture.snapshot.provided=[];});
    await button().click();await panel().getByText('null',{exact:true}).waitFor();assert.equal((await panel().innerText()).trim(),'null');
    await mediator().click();await mediatorPanel().getByText('null',{exact:true}).waitFor();assert.equal((await mediatorPanel().innerText()).trim(),'null');
    await page.reload();await debug().waitFor();await debug().click();
    // Pending -> persisted identical content must acquire its message ID.
    await page.getByRole('textbox',{name:'Message'}).fill('再聊一轮');await page.getByRole('button',{name:'Send',exact:true}).click();
    await page.getByText('这是合成的新一轮回复。',{exact:true}).waitFor();await debug().click();await button().click();
    await page.getByText('回复生成中，保存后可查看本轮参考片段。',{exact:true}).waitFor();
    await page.evaluate(()=>window.finishTurn());await panel().getByText(/本轮召回 2 段/).waitFor();
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({width,fit,lazy:true,independent:true,retry:true,timeout:true,history:true,streamEnrichment:true,errors}));
    await context.close();
  }}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
