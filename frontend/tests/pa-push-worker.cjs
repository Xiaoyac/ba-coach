// Exercise the actual service worker handlers in an isolated runtime.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
  const listeners={},store=new Map(),notifications=[],opened=[],receipts=[];
  let windows=[],failDisplay=false;
  const cache={match:async k=>store.has(k)?new Response(store.get(k)):undefined,
    put:async(k,r)=>store.set(k,await r.text()),delete:async k=>store.delete(k)};
  const self={location:{origin:'https://test.example'},addEventListener:(k,fn)=>listeners[k]=fn,
    skipWaiting:()=>{},clients:{claim:()=>{},matchAll:async()=>windows,openWindow:async url=>opened.push(url)},
    registration:{showNotification:async(title,opts)=>{if(failDisplay)throw Error('denied');notifications.push({title,...opts});},getNotifications:async()=>[]}};
  vm.runInNewContext(fs.readFileSync('public/pa-push-sw.js','utf8'),{self,caches:{open:async()=>cache},Response,URL,Date,AbortSignal,
    fetch:async(url,opts)=>{receipts.push({url,...opts});return new Response(null,{status:204});}});
  async function event(name,data){let pending;listeners[name]({...data,waitUntil:p=>pending=p});await pending;}
  const id='00000000-0000-4000-8000-000000000001';
  const payload={subscription_id:id,tag:'test',expires_at:Date.now()+100000,title:'private text',body:'secret',url:'https://evil.example'};
  await event('push',{data:{json:()=>payload}});assert.equal(notifications.length,0);
  await event('message',{source:{url:'https://evil.example'},data:{type:'PA_PUSH_BIND',id},ports:[]});assert.equal(store.size,0);
  await event('message',{source:{url:'https://test.example/'},data:{type:'PA_PUSH_BIND',id},ports:[]});
  await event('push',{data:{json:()=>({...payload,subscription_id:'other'})}});assert.equal(notifications.length,0);
  await event('push',{data:{json:()=>({...payload,expires_at:0})}});assert.equal(notifications.length,0);
  await event('push',{data:{json:()=>payload}});assert.equal(notifications.length,1);
  assert.ok(!JSON.stringify(notifications).includes('secret'));
  await event('notificationclick',{notification:{close:()=>{},data:notifications[0].data}});
  assert.equal(opened[0],'https://test.example/?pa_reminder=1');
  const diagnostic={...payload,kind:'delivery_check',check_id:'a'.repeat(64),receipt_token:'A'.repeat(43)};
  await event('push',{data:{json:()=>diagnostic}});
  assert.equal(receipts.length,1);assert.equal(JSON.parse(receipts[0].body).had_open_window,false);
  assert.equal(receipts[0].url,'/api/push/checks/receipt');assert.equal(receipts[0].credentials,'omit');
  await event('notificationclick',{notification:{close:()=>{},data:notifications[1].data}});
  assert.equal(opened[1],'https://test.example/?pa_push_check=1');
  windows=[{}];await event('push',{data:{json:()=>diagnostic}});
  assert.equal(JSON.parse(receipts[1].body).had_open_window,true);
  failDisplay=true;
  await assert.rejects(event('push',{data:{json:()=>diagnostic}}));assert.equal(receipts.length,2);
  failDisplay=false;
  const notificationCount=notifications.length;
  await event('message',{source:{url:'https://test.example/'},data:{type:'PA_PUSH_BIND',id:null},ports:[]});
  await event('push',{data:{json:()=>payload}});assert.equal(notifications.length,notificationCount);
  await event('push',{data:{json:()=>diagnostic}});assert.equal(receipts.length,2);
  assert.equal(listeners.fetch,undefined,'never cache authenticated pages/API');
  console.log('PASS: consent, isolation, expiry, privacy, safe click, revocation, no offline data cache, closed-page receipts, display failure never acknowledged');
})().catch(e=>{console.error(e);process.exitCode=1;});
