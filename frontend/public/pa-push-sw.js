/* Notifications only: deliberately no fetch handler or offline chat cache. */
const CONSENT_CACHE = 'bacoach-push-consent-v1';
const CONSENT_URL = '/__pa_push_consent__';
async function binding() {
  const response = await (await caches.open(CONSENT_CACHE)).match(CONSENT_URL);
  return response ? response.json() : null;
}
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('message', event => {
  if (!event.source || new URL(event.source.url).origin !== self.location.origin) return;
  if (event.data?.type !== 'PA_PUSH_BIND') return;
  event.waitUntil((async () => {
    const cache = await caches.open(CONSENT_CACHE);
    const id = event.data.id;
    if (typeof id === 'string' && /^[a-f0-9-]{36}$/.test(id)) {
      await cache.put(CONSENT_URL, new Response(JSON.stringify({id})));
    } else {
      await cache.delete(CONSENT_URL);
      for (const notification of await self.registration.getNotifications()) notification.close();
    }
    event.ports[0]?.postMessage({ok:true});
  })());
});
self.addEventListener('push', event => {
  event.waitUntil((async () => {
    let data;
    try { data = event.data?.json(); } catch { return; }
    const consent = await binding();
    if (!consent || consent.id !== data?.subscription_id || !(data.expires_at > Date.now())) return;
    // Never display arbitrary server text or target identifiers on a lock screen.
    const isCheck=data.kind==='delivery_check';
    let hadOpenWindow=null;
    if(isCheck) {
      try {hadOpenWindow=(await self.clients.matchAll({type:'window',includeUncontrolled:true})).length>0;} catch {}
    }
    await self.registration.showNotification(isCheck?'BA Coach · 通知接收测试':'BA Coach · 活动后提醒', {
      body:isCheck?'这是一条接收测试通知。你可以点击它，返回查看测试结果。':'如果方便，回来聊聊今天的活动吧。做了、没做或有变化，都可以。',
      icon:'/manifest-icon/192', tag:String(data.tag).slice(0,64), renotify:false,
      data:{is_check:isCheck, subscription_id:consent.id},
    });
    if(isCheck && /^[a-f0-9]{64}$/.test(data.check_id) && /^[A-Za-z0-9_-]{43}$/.test(data.receipt_token)) {
      // No login token or arbitrary URL. One narrow, short-lived capability,
      // delivered encrypted to this device. No endpoint/user content is sent.
      try {await fetch('/api/push/checks/receipt',{method:'POST',credentials:'omit',redirect:'error',cache:'no-store',
        headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(8000),
        body:JSON.stringify({check_id:data.check_id,token:data.receipt_token,had_open_window:hadOpenWindow})});} catch {}
    }
  })());
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const consent = await binding();
    if (!consent || consent.id !== event.notification.data?.subscription_id) return;
    const url = new URL(event.notification.data?.is_check?'/?pa_push_check=1':'/?pa_reminder=1', self.location.origin).href;
    // Open separately: never navigate away from an unsaved daily form/chat draft.
    await self.clients.openWindow(url);
  })());
});
