// Real Chromium service worker, synthetic DevTools push; NOT provider E2E.
// Fresh isolated browser context. Never uses a real account/subscription.
const assert=require('node:assert/strict');
exports.run=async function(browser,baseUrl='http://127.0.0.1:3000') {
  const origin=new URL(baseUrl).origin;
  assert.ok(['127.0.0.1','localhost'].includes(new URL(baseUrl).hostname),'local synthetic check only');
  const context=await browser.newContext({permissions:['notifications']});
  try {
    const controller=await context.newPage(); // about:blank is not a site window.
    const cdp=await context.newCDPSession(controller);
    const registrations=new Map();
    cdp.on('ServiceWorker.workerRegistrationUpdated',e=>e.registrations.forEach(r=>registrations.set(r.registrationId,r)));
    await cdp.send('ServiceWorker.enable');
    const page=await context.newPage();
    await page.goto(baseUrl);
    const id='00000000-0000-4000-8000-000000000001';
    await page.evaluate(async id=>{
      await navigator.serviceWorker.register('/pa-push-sw.js',{scope:'/'});
      const reg=await navigator.serviceWorker.ready;
      await new Promise(resolve=>{const channel=new MessageChannel();channel.port1.onmessage=resolve;
        reg.active.postMessage({type:'PA_PUSH_BIND',id},[channel.port2]);});
    },id);
    // Block the synthetic receipt before it reaches the local backend. Only
    // transport is replaced; the actual showNotification and client count run.
    let worker=context.serviceWorkers().find(w=>w.url()===origin+'/pa-push-sw.js');
    assert.ok(worker);
    await worker.evaluate(()=>{
      self.__testReceipts=[];
      self.fetch=async(url,options)=>{self.__testReceipts.push({url,body:JSON.parse(options.body)});return new Response(null,{status:204});};
    });
    await page.close();
    const before=await worker.evaluate(async()=>({windows:(await self.clients.matchAll({type:'window',includeUncontrolled:true})).length}));
    assert.equal(before.windows,0);
    const registration=[...registrations.values()].find(r=>r.scopeURL===origin+'/');
    assert.ok(registration,'registration discovered');
    await cdp.send('ServiceWorker.deliverPushMessage',{origin,registrationId:registration.registrationId,
      data:JSON.stringify({kind:'delivery_check',subscription_id:id,tag:'a'.repeat(64),check_id:'a'.repeat(64),
        receipt_token:'A'.repeat(43),expires_at:Date.now()+60000})});
    let proof;
    for(let i=0;i<30;i++) {
      proof=await worker.evaluate(async()=>({receipts:self.__testReceipts,notifications:(await self.registration.getNotifications()).length}));
      if(proof.receipts.length)break;
      await controller.waitForTimeout(100);
    }
    assert.equal(proof.receipts.length,1);
    assert.equal(proof.receipts[0].body.had_open_window,false);
    assert.equal(proof.receipts[0].url,'/api/push/checks/receipt');
    await worker.evaluate(async()=>{for(const n of await self.registration.getNotifications())n.close();});
    return {openSiteWindows:0,nativeShowNotificationResolved:true,notificationReadback:proof.notifications,
      receiptConfirmed:true,transport:'synthetic DevTools push, not real provider or OS visibility proof'};
  } finally {await context.close();}
};
if(require.main===module)(async()=>{
  const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{console.log(JSON.stringify(await exports.run(browser)));}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
