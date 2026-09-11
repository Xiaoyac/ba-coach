// No API mocks: local frontend -> SSH loopback -> isolated real-model backend.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const base='http://127.0.0.1:3120';
  const bootstrap=await (await fetch(base+'/api/acceptance/bootstrap')).json();
  assert(bootstrap.token && bootstrap.session_id);
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  try {
    const page=await browser.newPage({viewport:{width:390,height:844}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(token=>localStorage.setItem('psy-auth-token',token),bootstrap.token);
    await page.addInitScript(()=>{
      window.__acceptanceStreams=[];
      const original=window.fetch.bind(window);
      window.fetch=async (...args)=>{
        const r=await original(...args);
        if(String(args[0]).includes('/api/chat/stream')) {
          r.clone().text().then(text=>window.__acceptanceStreams.push({text,status:r.status}));
        }
        return r;
      };
    });
    await page.goto(base);
    const input=page.getByLabel('Message',{exact:true});
    await input.waitFor({timeout:30000});
    // Exercise the actual UI's streaming request, not a page.route fixture.
    await input.fill('请先简单说明记录什么，不要替我确认新的约定。');
    const responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/chat/stream'),{timeout:180000});
    await page.getByLabel('Send',{exact:true}).click();
    const response=await responsePromise;
    assert.equal(response.status(),200);
    await page.waitForFunction(()=>window.__acceptanceStreams.length>0,{},{timeout:180000});
    const stream=await page.evaluate(()=>window.__acceptanceStreams[0].text);
    const frames=stream.split(/\r?\n\r?\n/).map(frame=>{
      const lines=frame.split(/\r?\n/);const e=lines.find(x=>x.startsWith('event: '));const d=lines.find(x=>x.startsWith('data: '));
      return e&&d?{type:e.slice(7),data:JSON.parse(d.slice(6))}:null;
    }).filter(Boolean);
    assert(!frames.some(f=>f.type==='error'));
    assert(frames.some(f=>f.type==='persisted'&&f.data.saved));
    const reply=frames.filter(f=>f.type==='delta').map(f=>f.data.text||'').join('');
    assert(reply.length>0);
    const api=await (await fetch(base+'/api/conversations/'+bootstrap.session_id,{headers:{Authorization:'Bearer '+bootstrap.token}})).json();
    assert(api.messages.some(m=>m.role==='assistant'&&m.content===reply));
    await page.reload();
    await input.waitFor({timeout:30000});
    const visiblePrefix=reply.split(/\r?\n/).find(s=>s.trim()).replace(/[*#`]/g,'').trim();
    await page.getByText(visiblePrefix,{exact:false}).first().waitFor({timeout:30000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
    await page.screenshot({path:process.env.TEMP+'/bacoach-real-backend-acceptance.png',fullPage:true});
    console.log(JSON.stringify({passed:true,api_mocks:false,stream_saved:true,refresh_preserved:true,browser_errors:errors.length,reply}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
