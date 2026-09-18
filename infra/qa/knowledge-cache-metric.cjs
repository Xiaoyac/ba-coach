// Intercepted synthetic administrator: no production accounts or API writes.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const url = process.env.CACHE_QA_URL || 'http://127.0.0.1:3000';
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  const out=path.resolve('.test-tmp',url.startsWith('https:')?'cache-metric-online':'cache-metric-local');
  await fs.mkdir(out,{recursive:true});
  try {for(const width of [1440,390,360]) {
    const context=await browser.newContext({viewport:{width,height:900},isMobile:width<600,hasTouch:width<600});
    await context.addInitScript(()=>{
      localStorage.setItem('psy-auth-token','synthetic');
      const original=window.fetch.bind(window);
      window.metricFixture={mode:'normal',calls:0};
      window.fetch=async (input,options={})=>{
        const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
        const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
        const detail={session_id:'metric-demo',title:'缓存指标验收',next_module:'module_1',revision:1,messages:[],updated_at:'2026-09-17T12:00:00Z'};
        if(p==='/api/auth/me')return json({username:'synthetic',nickname:'合成管理员',role:'admin',profile_uuid:'synthetic',email_required:false,email_verified:true});
        if(p==='/api/conversations')return json([detail]);
        if(p==='/api/conversations/metric-demo')return json(detail);
        if(p.endsWith('/revision'))return json({revision:1});
        if(p.endsWith('/events'))return new Response(new ReadableStream({start(){},cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
        if(p==='/api/admin/knowledge/cache-stats'){
          const f=window.metricFixture;f.calls++;
          if(f.mode==='error')return json({},503);
          if(f.mode==='measured')return json(f.measured);
          return json({enabled:f.mode!=='disabled',scope:'process',started_at:'2026-09-18T00:00:00Z',requests:f.mode==='empty'?0:8,hits:['empty','zero'].includes(f.mode)?0:2,hit_rate:f.mode==='empty'?null:f.mode==='zero'?0:.25,coalesced:f.mode==='empty'?0:1,entries:4,
            ttl_seconds:900,empty_ttl_seconds:45,saved_model_calls:4,coalesced_saved_model_calls:2,invalidations:1,
            miss_reasons:{first_or_untracked:2,expired:1,invalidated:1,uncacheable:1},
            diagnostics:{unchanged_index_refreshes:3},public_preparation:{hits:3,requests:5,hit_rate:.6,entries:1}});
        }
        if(p.startsWith('/api/program/'))return json({enabled:false});
        if(p.startsWith('/api/'))return json({});
        return original(input,options);
      };
    });
    const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(url);
    if(width<600)await page.getByRole('button',{name:'切换对话侧栏'}).click();
    await page.getByRole('button',{name:/管理工具/}).waitFor();
    assert.equal(await page.evaluate(()=>window.metricFixture.calls),0);
    await page.getByRole('button',{name:/管理工具/}).click();
    const metric=page.getByTestId('knowledge-cache-metric');
    await metric.getByText('25.0%',{exact:true}).waitFor();
    assert.ok((await metric.boundingBox()).height<60);
    await page.screenshot({path:path.join(out,`compact-${width}.png`)});
    await metric.locator('summary').click();
    await metric.getByText(/有效命中 ÷/).waitFor();
    assert.match(await metric.innerText(),/命中 2 \/ 查询 8/);
    await metric.getByText('未命中原因',{exact:true}).waitFor();
    await metric.getByText('相同查询已过期',{exact:true}).waitFor();
    await metric.getByText('公共目录准备复用',{exact:true}).waitFor();
    assert.match(await metric.innerText(),/不计入上方命中率/);
    assert.ok((await metric.getByTestId('knowledge-cache-details').boundingBox()).height<=540);
    await page.screenshot({path:path.join(out,`details-${width}.png`)});
    await metric.locator('summary').click();
    for(const [mode,expected] of [['empty','暂无数据'],['zero','0.0%'],['disabled','已关闭'],['error','暂不可用'],['normal','25.0%']]){
      await page.evaluate(value=>window.metricFixture.mode=value,mode);
      await metric.getByRole('button',{name:'刷新缓存命中率'}).click();
      await metric.getByText(expected,{exact:true}).waitFor();
    }
    // Optional real counters from the offline policy replay, not fabricated UI numbers.
    if(process.env.CACHE_QA_COMPARE_FILE){
      const comparison=JSON.parse(await fs.readFile(process.env.CACHE_QA_COMPARE_FILE,'utf8'));
      await page.evaluate(value=>{window.metricFixture.mode='measured';window.metricFixture.measured=value;},comparison.scenarios.audit_boundary.new.monitoring);
      await metric.getByRole('button',{name:'刷新缓存命中率'}).click();
      await metric.getByText('50.0%',{exact:true}).waitFor();
      await metric.locator('summary').click();
      await page.screenshot({path:path.join(out,`measured-${width}.png`)});
      await metric.locator('summary').click();
    }
    await page.getByRole('button',{name:'切换到暗色主题'}).click();
    await page.waitForTimeout(700);
    await page.screenshot({path:path.join(out,`dark-${width}.png`)});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.ok(await metric.evaluate(e=>e.scrollWidth<=e.clientWidth));
    await page.getByRole('button',{name:/管理工具/}).click();assert.equal(await metric.count(),0);
    assert.deepEqual(errors,[]);
    await context.close();
    console.log(`PASS ${width}: compact/details/empty/zero/disabled/error/retry/dark/no-overflow`);
  }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
