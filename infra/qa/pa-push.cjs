// Synthetic APIs and browser permission/PushManager. No external subscriptions.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
exports.run=async function(browser,baseUrl='http://127.0.0.1:3000') {
  const out=path.resolve(__dirname,'../../.test-tmp/pa-push');await fs.mkdir(out,{recursive:true});
  const reports=[];
  for(const [width,theme,mode] of [[1440,'warm','normal'],[390,'warm','normal'],[360,'dark','normal'],
    [390,'warm','ios'],[390,'warm','denied'],[1440,'dark','disabled'],[390,'warm','save-fail'],[390,'warm','preference']]) {
    const context=await browser.newContext({viewport:{width,height:850},isMobile:width<600,hasTouch:width<600});
    try {
      await context.addInitScript(({theme,mode})=>{
        localStorage.setItem('psy-auth-token','synthetic');localStorage.setItem('psy-theme',theme);
        const f=window.pushFixture={requests:0,devices:[],checks:[],bound:null,subscriptions:0,unsubscribed:0,saveFail:mode==='save-fail'};
        const id='00000000-0000-4000-8000-000000000001';
        if(mode==='ios')Object.defineProperty(navigator,'userAgent',{value:'iPhone Safari'});
        class FakeNotification {static permission=mode==='denied'?'denied':'default';static async requestPermission(){f.requests++;this.permission='granted';return 'granted';}}
        Object.defineProperty(window,'Notification',{value:FakeNotification,configurable:true});
        let sub=null;
        const makeSub=()=>({toJSON:()=>({endpoint:'https://fcm.googleapis.com/synthetic',keys:{p256dh:'synthetic',auth:'synthetic'},expirationTime:null}),unsubscribe:async()=>{f.unsubscribed++;sub=null;return true;}});
        const reg={update:async()=>{},active:{scriptURL:location.origin+'/pa-push-sw.js',postMessage:(message,ports)=>{f.bound=message.id;ports[0].postMessage({ok:true});}},
          pushManager:{getSubscription:async()=>sub,subscribe:async()=>{f.subscriptions++;return sub=makeSub();}}};
        Object.defineProperty(navigator,'serviceWorker',{configurable:true,value:{register:async()=>reg,ready:Promise.resolve(reg),getRegistration:async()=>reg}});
        const original=window.fetch.bind(window);
        const detail={session_id:'fixture',title:'提醒测试',revision:1,updated_at:'2026-09-18T01:00:00Z',next_module:'module_2',messages:[]};
        const json=(v,status=200)=>new Response(status===204?null:JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});
        window.fetch=async(input,options={})=>{
          const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
          if(p==='/api/auth/me')return json({username:'fixture',nickname:'演示用户',role:'user',profile_uuid:'fixture',email_required:false,email_verified:true});
          if(p==='/api/conversations')return json([detail]);
          if(p==='/api/conversations/fixture')return json(detail);
          if(p.endsWith('/revision'))return json({revision:1});
          if(p.endsWith('/events'))return new Response(new ReadableStream({start(){},cancel(){}}),{headers:{'Content-Type':'text/event-stream'}});
          if(p==='/api/program/goals/overview')return json({enabled:true,goals:[]});
          if(p==='/api/push/status')return json({available:mode!=='disabled',preference_blocked:mode==='preference',public_key:'B'+ 'A'.repeat(86),devices:f.devices,
            upcoming:[{goal_id:'fake',start_at:'2026-09-18T08:00:00Z',due_at:'2026-09-18T08:30:00Z'}]});
          if(p==='/api/push/checks'&&options.method==='POST') {
            if(f.checks.length)return json({detail:'请隔5分钟再测试，每24小时最多5次。'},429);
            const c={id:'a'.repeat(64),device_id:id,state:'queued',due_at:new Date(Date.now()+60000).toISOString(),
              expires_at:new Date(Date.now()+660000).toISOString(),displayed_at:null,had_open_window:null,http_status:null};
            f.checks=[c];return json(c,202);
          }
          if(p==='/api/push/checks')return json({items:f.checks});
          if(p==='/api/push/subscriptions'&&options.method==='POST') {
            if(f.saveFail)return json({detail:'保存失败，请重试。'},503);
            f.devices=[{id,this_session:true}];return json({id},201);
          }
          if(p.startsWith('/api/push/subscriptions')&&options.method==='DELETE'){f.devices=[];return json(null,204);}
          if(p==='/api/auth/logout')return json(null,204);
          if(p.startsWith('/api/'))return json({});
          return original(input,options);
        };
      },{theme,mode});
      const page=await context.newPage(),errors=[];page.on('pageerror',e=>{errors.push(e.message);console.error('PAGEERROR',e.message);});
      const deepLink=width===1440&&mode==='normal';
      await page.goto(deepLink?new URL('/?pa_push_check=1',baseUrl).href:baseUrl);
      if(!deepLink) {
        await page.getByRole('button',{name:/账号菜单/}).click();
        await page.getByRole('menuitem',{name:'活动后提醒',exact:true}).click();
      }
      const modal=page.getByRole('dialog',{name:'活动后提醒',exact:true});
      const enable=modal.getByRole('button',{name:'在此浏览器开启提醒',exact:true});
      await enable.waitFor();await page.waitForTimeout(200);
      assert.equal(await page.evaluate(()=>window.pushFixture.requests),0,'never prompt on page load');
      if(['ios','denied','disabled','preference'].includes(mode)) {
        assert.equal(await enable.isDisabled(),true);
        await modal.getByText(mode==='ios'?/iPhone \/ iPad/ : mode==='denied'?/通知权限已被拒绝/ : mode==='preference'?/你的档案选择了不主动提醒/ : /推送服务尚未启用/).waitFor();
      } else {
        await enable.click();
        if(mode==='save-fail') {
          await modal.getByRole('alert').getByText('保存失败，请重试。',{exact:true}).waitFor();
          assert.equal(await page.evaluate(()=>window.pushFixture.bound),null);
          assert.equal(await page.evaluate(()=>window.pushFixture.devices.length),0);
          await page.evaluate(()=>window.pushFixture.saveFail=false);await enable.click();
        }
        await modal.getByRole('button',{name:'关闭此浏览器提醒',exact:true}).waitFor();
        assert.equal(await page.evaluate(()=>window.pushFixture.devices.length),1);
        await modal.getByText('已识别的近期提醒时间',{exact:true}).waitFor();
        await modal.getByRole('button',{name:'一分钟后测试通知',exact:true}).click();
        await modal.getByText(/已预约。现在可以关闭本站所有标签页/).waitFor();
        await modal.getByRole('button',{name:'一分钟后测试通知',exact:true}).click();
        await modal.getByRole('alert').getByText(/请隔5分钟再测试/).waitFor();
        await page.evaluate(()=>{window.pushFixture.checks[0].state='accepted';});
        await modal.getByRole('button',{name:'查看测试结果',exact:true}).click();
        await modal.getByText(/推送服务已接收，等待浏览器确认/).waitFor();
        await page.evaluate(()=>{const c=window.pushFixture.checks[0];c.displayed_at=new Date().toISOString();c.had_open_window=true;});
        await modal.getByRole('button',{name:'查看测试结果',exact:true}).click();
        await modal.getByText(/当时仍有本站窗口/).waitFor();
        await page.evaluate(()=>{window.pushFixture.checks[0].had_open_window=false;});
        await modal.getByRole('button',{name:'查看测试结果',exact:true}).click();
        await modal.getByText(/关页接收已验证/).waitFor();
        if(mode==='save-fail') {
          await page.evaluate(()=>{const f=window.pushFixture;f.devices=[];Object.assign(f.checks[0],{state:'failed',displayed_at:null,http_status:410});});
          await modal.getByRole('button',{name:'查看测试结果',exact:true}).click();
          await modal.getByText(/推送服务拒绝了请求/).waitFor();
          assert.equal(await modal.getByRole('button',{name:'一分钟后测试通知',exact:true}).isDisabled(),true);
          await page.evaluate(()=>{const f=window.pushFixture;f.devices=[{id:f.checks[0].device_id,this_session:true}];});
          await modal.getByRole('button',{name:'查看测试结果',exact:true}).click();
          await modal.getByRole('button',{name:'关闭此浏览器提醒',exact:true}).waitFor();
        }
      }
      await modal.evaluate(el=>el.scrollTop=0);
      await page.screenshot({path:path.join(out,`${width}-${theme}-${mode}.png`),scale:'css'});
      const bounds=await modal.boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width&&bounds.y>=0&&bounds.y+bounds.height<=850);
      if(!['ios','denied','disabled','preference'].includes(mode)) {
        await modal.getByRole('button',{name:'关闭所有设备提醒'}).click();
        await enable.waitFor();assert.equal(await page.evaluate(()=>window.pushFixture.bound),null);
        assert.equal(await page.evaluate(()=>window.pushFixture.devices.length),0);
        await enable.click();await modal.getByRole('button',{name:'关闭此浏览器提醒',exact:true}).waitFor();
        await modal.getByRole('button',{name:'关闭此浏览器提醒',exact:true}).click();await enable.waitFor();
      }
      await modal.getByRole('button',{name:'关闭提醒设置'}).click();await modal.waitFor({state:'hidden'});
      if(width<768)await page.getByRole('button',{name:'切换对话侧栏'}).click();
      await page.getByRole('button',{name:'我的目标',exact:true}).click();
      await page.getByRole('button',{name:'活动后提醒',exact:true}).click();await modal.waitFor();
      await page.keyboard.press('Escape');await modal.waitFor({state:'hidden'});
      await page.getByRole('dialog',{name:'我的目标',exact:true}).waitFor();
      if(mode==='normal') {
        await page.getByRole('button',{name:'活动后提醒',exact:true}).click();await modal.waitFor();
        await enable.click();await modal.getByRole('button',{name:'关闭此浏览器提醒',exact:true}).waitFor();
        await modal.getByRole('button',{name:'关闭提醒设置'}).click();
        await page.getByRole('button',{name:'关闭目标总览'}).click();
        await page.getByRole('button',{name:/账号菜单/}).click();
        await page.getByRole('menuitem',{name:'退出登录',exact:true}).click();
        await page.waitForFunction(()=>!localStorage.getItem('psy-auth-token'));
        assert.equal(await page.evaluate(()=>window.pushFixture.bound),null);
        assert.equal(await page.evaluate(()=>localStorage.getItem('bacoach-push-device')),null);
      }
      assert.deepEqual(errors,[]);reports.push({width,theme,mode,passed:true});
    } finally {await context.close();}
  }
  return reports;
};
if(require.main===module)(async()=>{
  const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{console.log(JSON.stringify(await exports.run(browser)));}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
