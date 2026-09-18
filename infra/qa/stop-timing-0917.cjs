// Real frontend bundle, synthetic API only. No production data or model calls.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async () => {
  const bubbleQa = process.env.BUBBLE_QA === '1';
  const statusQa = process.env.STATUS_QA === '1';
  const out = path.resolve(statusQa ? '.test-tmp/generation-status-0917' : bubbleQa ? '.test-tmp/assistant-bubble-0917' : '.test-tmp/stop-timing-0917'); await fs.mkdir(out, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  try { for (const [width, theme] of (bubbleQa ? [[1440,'warm'],[390,'warm'],[360,'warm'],[1440,'dark'],[390,'dark']] : [[1440,'warm'],[390,'warm'],[360,'warm']])) {
    const context = await browser.newContext({viewport:{width,height:900},isMobile:width<600,hasTouch:width<600});
    await context.addInitScript(({theme,bubbleQa,statusQa}) => {
      localStorage.setItem('psy-auth-token', 'synthetic');
      localStorage.setItem('psy-theme', theme);
      if(statusQa){const now=Date.now.bind(Date);window.qaClockOffset=0;Date.now=()=>now()+window.qaClockOffset;}
      const original = window.fetch.bind(window);
      const f = window.fixture = {revision:1, mode:'waiting', cancelCalls:0, ids:[], messages:[
        {role:'user',content:bubbleQa?'今天下班后有点累，但又想出去走走。':'这是已有问题'},
        {role:'assistant',content:bubbleQa?'听起来你想给自己一点放松的时间，也想照顾今天的体力。\n\n**不用一下子做很多。** 我们可以从一个轻松的小尝试开始：\n\n- 换上舒服的鞋，走到楼下。\n- 先散步五分钟，看看身体的感觉。\n\n你觉得这样的开始，对今天的你来说合适吗？':'这是已有回复',reasoning_content:'已有独立思考',model_name:'synthetic'},
      ]};
      const detail = () => ({session_id:'stop-demo',title:'停止与计时验收',revision:f.revision,messages:f.messages,
        updated_at:'2026-09-17T04:00:00Z',next_module:'module_1'});
      const json = (body, status=200) => new Response(JSON.stringify(body), {status,headers:{'Content-Type':'application/json'}});
      window.fetch = async (input, options={}) => {
        const url = new URL(typeof input==='string'?input:input.url, location.href).pathname;
        if (url==='/api/auth/me') return json({username:'synthetic',nickname:'本地验收',role:'user',profile_uuid:'synthetic',email_required:false,email_verified:true});
        if (url==='/api/conversations') return json([detail()]);
        if (url==='/api/conversations/stop-demo') return json(detail());
        if (url.endsWith('/revision')) return json({revision:f.revision});
        if (url.endsWith('/events')) return new Response(new ReadableStream({start(c){
          window.enrich = () => {
            f.messages[1].timing = {reply_thinking_ms:7500,reply_generation_ms:9100,router_processing_ms:2000};
            f.revision++;
            c.enqueue(new TextEncoder().encode('event: snapshot\ndata: '+JSON.stringify(detail())+'\n\n'));
          };
        },cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
        if (url==='/api/program/stop-demo') return json({enabled:false});
        if (url==='/api/chat/cancel') {
          f.cancelCalls++;
          f.lastCancel = JSON.parse(options.body).generation_id;
          if (f.mode==='cancel-fail') return json({},503);
          await new Promise(resolve=>setTimeout(resolve,400));
          if (f.mode==='finalizing') { window.finish(); return json({status:'finished'}); }
          window.cancelStream(); return json({status:'cancelled'});
        }
        if (url==='/api/chat/stream') {
          const body=JSON.parse(options.body); f.ids.push(body.generation_id);
          f.messages.push({role:'user',content:body.message}); f.revision++;
          return new Response(new ReadableStream({start(c) {
            let ended=false;
            const emit = (event, data) => { if (!ended) c.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)); };
            emit('meta',{session_id:'stop-demo',model:'synthetic',routing_pending:false});
            window.partialReply = () => emit('delta',{text:'这是一段仍在生成的回复。'});
            window.finish = () => {
              const msg={role:'assistant',content:'已完成的新回复',reasoning_content:'新回复的思考',model_name:'synthetic',
                timing:{reply_thinking_ms:1200,reply_generation_ms:2300,router_processing_ms:null}};
              f.messages.push(msg);f.revision++;
              emit('delta',{text:msg.content}); emit('reasoning_delta',{text:msg.reasoning_content});
              emit('persisted',{saved:true});ended=true;c.close();
            };
            window.cancelStream = () => {emit('cancelled',{cancelled:true,session_id:'stop-demo'});ended=true;c.close();};
            options.signal?.addEventListener('abort',()=>{if(!ended){ended=true;c.error(new DOMException('aborted','AbortError'));}});
          },cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
        }
        if (url.startsWith('/api/')) return json({});
        return original(input,options);
      };
    },{theme,bubbleQa,statusQa});
    const page=await context.newPage(), errors=[];page.on('pageerror',e=>errors.push(e.message));
    const shot=async name=>{await page.waitForTimeout(600);await page.screenshot({path:path.join(out,`${name}-${width}${bubbleQa?'-'+theme:''}.png`),scale:'css'});};
    await page.goto('http://127.0.0.1:3000');
    if (bubbleQa) {
      const bubble=page.locator('[data-message-bubble="assistant"]').first();await bubble.waitFor();
      const style=await bubble.evaluate(el=>{const c=getComputedStyle(el),r=el.getBoundingClientRect();return {bg:c.backgroundColor,border:c.borderTopWidth,radius:c.borderBottomRightRadius,left:r.left,right:r.right,width:innerWidth};});
      assert.notEqual(style.bg,'rgba(0, 0, 0, 0)');assert.equal(style.border,'0px');assert.ok(parseFloat(style.radius)>0);assert.ok(style.left>=0&&style.right<=style.width);
      const userBg=await page.locator('[data-message-bubble="user"]').first().evaluate(el=>getComputedStyle(el).backgroundColor);assert.notEqual(style.bg,userBg);
      await shot('conversation');
      await bubble.locator('../..').getByRole('button',{name:'复制消息',exact:true}).click();
      await page.getByText('消息已复制到剪贴板',{exact:true}).waitFor();
    }
    await page.getByRole('button',{name:'调试详情',exact:true}).first().click();
    await page.getByRole('button',{name:'查看回复深度思考'}).click();
    await page.getByText('思考阶段耗时（估算）：未记录',{exact:true}).waitFor();
    await page.evaluate(()=>window.enrich());
    await page.getByText('思考阶段耗时（估算）：7.5 秒',{exact:true}).waitFor();
    await page.getByText('回复总耗时：9.1 秒',{exact:true}).waitFor();
    await shot('timing-enriched');
    if(bubbleQa){
      await page.evaluate(()=>{window.fixture.messages[1].content='## 较长内容检查\n\n'+('长一些的回复也应保持自然换行。'.repeat(20))+'\n\n```text\n'+('sample'.repeat(160))+'\n```';window.enrich();});
      await page.getByRole('heading',{name:'较长内容检查'}).waitFor();
      const bounds=await page.locator('[data-message-bubble="assistant"]').first().evaluate(el=>{const r=el.getBoundingClientRect();return {right:r.right,width:innerWidth};});assert.ok(bounds.right<=bounds.width);
      await shot('long-markdown');
    }
    const input=page.getByRole('textbox',{name:'Message'});
    if(statusQa){
      await page.getByText('内容由 AI 生成，仅供参考，不能替代专业建议。',{exact:true}).waitFor();
      assert.equal(await page.getByText('Enter 发送 · Shift + Enter 换行',{exact:true}).count(),0);
      await input.fill('换行检查');await input.press('Shift+Enter');assert.match(await input.inputValue(),/\n/);
      assert.equal(await page.getByRole('button',{name:'停止生成',exact:true}).count(),0);
    }
    await input.fill('等待时停止');await input.press('Enter');
    const stop=page.getByRole('button',{name:'停止生成',exact:true});await stop.waitFor();
    if(statusQa){
      const status=page.locator('[data-generation-status]');await status.waitFor();
      assert.equal(await status.evaluate(el=>!!el.closest('[data-message-bubble]')),false);
      assert.equal(await page.locator('[data-message-bubble="assistant"]').count(),1);
      await shot('preparing-outside');
      await page.evaluate(()=>window.qaClockOffset=21000);
      await page.getByText(/仍在生成，已等待 \d+ 秒；请勿重复提交/).waitFor();
      await shot('elapsed-outside');
      await page.evaluate(()=>window.partialReply());
      await page.getByText('这是一段仍在生成的回复。',{exact:true}).waitFor();
      assert.equal(await status.evaluate(el=>!!el.closest('[data-message-bubble]')),false);
      assert.equal(await page.locator('[data-message-bubble="assistant"]').count(),2);
      await shot('streaming-outside');
      await page.evaluate(()=>window.qaClockOffset=0);
    }
    await shot('stop-ready');
    await input.fill('下一条草稿');
    await stop.click();
    const stopping=page.getByRole('button',{name:'正在停止生成',exact:true});await stopping.waitFor();
    assert.equal(await stopping.isDisabled(),true);
    await page.getByRole('status').filter({hasText:'已停止生成'}).waitFor();
    await page.getByRole('button',{name:'Send',exact:true}).waitFor();
    if(statusQa)assert.equal(await page.locator('[data-generation-status]').count(),0);
    assert.equal(await input.inputValue(),'下一条草稿');
    assert.equal(await page.evaluate(()=>window.fixture.cancelCalls),1);
    assert.equal(await page.evaluate(()=>window.fixture.lastCancel===window.fixture.ids[0]),true);
    assert.equal(await page.getByText(/等待回复超时|保存失败/).count(),0);
    await shot('stopped');
    await input.press('Enter');await stop.waitFor();
    await page.evaluate(()=>window.finish());
    await page.getByRole('button',{name:'Send',exact:true}).waitFor();
    await page.getByRole('button',{name:'调试详情',exact:true}).last().click();
    await page.getByRole('button',{name:'查看回复深度思考'}).last().click();
    await page.getByText('思考阶段耗时（估算）：1.2 秒',{exact:true}).waitFor();
    await page.getByText('思考阶段耗时（估算）：1.2 秒',{exact:true}).scrollIntoViewIfNeeded();
    await shot('resumed-timing');
    // A failed stop is retryable, not falsely reported as cancelled.
    await page.evaluate(()=>window.fixture.mode='cancel-fail');
    await input.fill('失败后再停止');await input.press('Enter');await stop.click();
    await page.getByText('停止请求未确认，请重试。当前回复可能仍在生成。',{exact:true}).waitFor();
    assert.equal(await stop.isEnabled(),true);
    await page.evaluate(()=>window.fixture.mode='waiting');
    await stop.click();await page.getByRole('button',{name:'Send',exact:true}).waitFor();
    // Finishing won the race: preserve the completed reply and don't claim a stop.
    await page.evaluate(()=>window.fixture.mode='finalizing');
    await input.fill('完成边界');await input.press('Enter');await stop.click();
    await page.getByRole('button',{name:'Send',exact:true}).waitFor();
    assert.equal(await page.getByRole('status').filter({hasText:'已停止生成'}).count(),0);
    assert.equal(await page.getByText('已完成的新回复',{exact:true}).count(),2);
    const fit=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    assert.equal(fit.width,fit.scroll);assert.deepEqual(errors,[]);
    assert.equal(await page.evaluate(()=>new Set(window.fixture.ids).size===window.fixture.ids.length),true);
    console.log(JSON.stringify({width,theme,bubbleQa,timingOnlySnapshot:true,stopAndResume:true,draftPreserved:true,retryStop:true,completionRace:true,fit,errors}));
    await context.close();
  }} finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
