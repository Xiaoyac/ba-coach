// Synthetic API only. Check that ordinary conversation sync paints a reminder.
const assert = require('node:assert/strict');
exports.run = async function(browser, baseUrl='http://127.0.0.1:3016') {
  const results=[];
  for (const width of [1440,390]) {
    const context=await browser.newContext({viewport:{width,height:900}});
    await context.addInitScript(()=>{
      localStorage.setItem('psy-auth-token','synthetic');
      const json=v=>new Response(JSON.stringify(v),{headers:{'Content-Type':'application/json'}});
      const original=window.fetch.bind(window);
      const fixture=window.reminderFixture={
        session_id:'reminder-demo',title:'站内提醒测试',revision:1,next_module:'module_3',
        updated_at:'2026-09-29T00:00:00Z',
        messages:[{role:'assistant',content:'活动后可以记录。'}],
      };
      let controller;
      window.publishReminder=()=>{
        fixture.messages.push({role:'assistant',content:'活动后提醒：按原计划，现在已过约一小时。可以打开「每日记录」记下活动与心情。'});
        fixture.revision++;
        controller?.enqueue(new TextEncoder().encode('event: snapshot\ndata: '+JSON.stringify(fixture)+'\n\n'));
      };
      window.fetch=async(input,options={})=>{
        const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
        if(p==='/api/auth/me')return json({username:'synthetic',nickname:'提醒验收',role:'user',profile_uuid:'synthetic',email_required:false,email_verified:true});
        if(p==='/api/conversations')return json(options.method==='POST'?fixture:[fixture]);
        if(p==='/api/conversations/current'||p==='/api/conversations/reminder-demo')return json(fixture);
        if(p.endsWith('/revision'))return json({revision:fixture.revision});
        if(p.endsWith('/events'))return new Response(new ReadableStream({start(c){controller=c;},cancel(){controller=null;}}),{headers:{'Content-Type':'text/event-stream'}});
        if(p==='/api/assessment/by-date')return json(null);
        if(p.startsWith('/api/'))return json({});
        return original(input,options);
      };
    });
    const page=await context.newPage(),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(baseUrl);
    await page.getByText('活动后可以记录。',{exact:true}).waitFor();
    // No user input, no generation request, no reload: a server revision arrives.
    await page.evaluate(()=>window.publishReminder());
    const reminder=page.getByText('活动后提醒：按原计划，现在已过约一小时。可以打开「每日记录」记下活动与心情。',{exact:true});
    await reminder.waitFor(); assert.equal(await reminder.count(),1);
    await page.getByRole('button',{name:'打开每日记录',exact:true}).click();
    await page.getByRole('dialog',{name:'每日行为记录',exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    results.push({width,liveReminder:true,opensRecord:true});
    await context.close();
  }
  return results;
};
