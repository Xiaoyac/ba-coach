import { API_BASE } from '@/lib/api';
import { apiHeaders, checkAuthentication } from '@/lib/http';
const BINDING_KEY = 'bacoach-push-device';
export type PushStatus = {available:boolean; public_key?:string; preference_blocked?:boolean; devices:{id:string;this_session:boolean}[];
  upcoming:{goal_id:string;start_at:string;due_at:string}[]};

async function request(path:string, method='GET', body?:unknown) {
  const response = await fetch(`${API_BASE}/api/push/${path}`, {method, headers:apiHeaders({json:body!==undefined}),
    ...(body!==undefined?{body:JSON.stringify(body)}:{}), cache:'no-store', signal:AbortSignal.timeout(12000)});
  checkAuthentication(response);
  if(!response.ok) { const data=await response.json().catch(()=>({})); throw new Error(typeof data.detail==='string'?data.detail:'提醒设置未能保存，请重试。'); }
  return response.status===204 ? null : response.json();
}
export const readPushStatus = ():Promise<PushStatus> => request('status');
export const localPushId = () => localStorage.getItem(BINDING_KEY);
export type PushCheck = {id:string;device_id:string;state:string;due_at:string;expires_at:string;
  displayed_at:string|null;had_open_window:boolean|null;http_status:number|null};
export const readPushChecks = ():Promise<{items:PushCheck[]}> => request('checks');
export async function schedulePushCheck():Promise<PushCheck> {
  const id=localPushId();
  if(!id) throw new Error('请先在此浏览器开启提醒。');
  const reg=await navigator.serviceWorker.getRegistration('/');
  if(!reg?.active?.scriptURL.endsWith('/pa-push-sw.js')) throw new Error('通知服务尚未就绪，请重新开启提醒。');
  // Existing subscribers need the receipt-capable worker too; no new permission.
  await bounded(reg.update());
  return request('checks','POST',{device_id:id});
}

export function pushCheckMessage(check:PushCheck):string {
  if(check.displayed_at) return check.had_open_window===false
    ?'关页接收已验证：接收时没有本站窗口，浏览器报告通知显示成功。'
    :check.had_open_window===true?'浏览器报告通知显示成功，但当时仍有本站窗口，尚未验证关页接收。'
    :'浏览器报告通知显示成功，无法确定当时是否关闭网页。';
  if(check.state==='cancelled') return '测试已取消：通知订阅、登录状态或提醒偏好发生了变化。';
  if(check.state==='failed') return '推送服务拒绝了请求，尚未确认送达。请检查权限，必要时重新开启提醒。';
  if(new Date(check.expires_at).getTime()<=Date.now() || check.state==='expired') return '测试窗口已结束，没有收到浏览器确认。这不一定表示通知未显示，请同时检查系统通知中心。';
  if(check.state==='accepted') return '推送服务已接收，等待浏览器确认；这还不代表设备收到通知。';
  if(check.state==='unknown') return '发送结果不明，等待浏览器确认；不会自动重发，避免重复打扰。';
  if(check.state==='attempting') return '服务器正在发送，暂未收到浏览器确认。';
  return '已预约。现在可以关闭本站所有标签页或离开主屏幕应用，不要退出登录；约一分钟后留意系统通知。';
}

export function pushSupport():string|null {
  const ios=/iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
  const standalone=matchMedia('(display-mode: standalone)').matches || Boolean((navigator as Navigator & {standalone?:boolean}).standalone);
  if(ios&&!standalone) return 'iPhone / iPad：请在 Safari 的分享菜单中选择「添加到主屏幕」，从主屏幕打开后再开启提醒。';
  if(!window.isSecureContext) return '推送需要安全连接，请通过 HTTPS 打开网站。';
  if(!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window))
    return '当前浏览器不支持网页推送。请尝试支持通知的独立浏览器；微信内打开通常无法使用。';
  if(Notification.permission==='denied') return '通知权限已被拒绝。请先在浏览器的网站设置中允许通知，再重新开启。';
  return null;
}

function bounded<T>(promise:Promise<T>, ms=15000):Promise<T> {
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('浏览器推送服务连接超时，请检查网络后重试。')),ms);
    promise.then(value=>{clearTimeout(timer);resolve(value);},error=>{clearTimeout(timer);reject(error);});
  });
}

async function bind(reg:ServiceWorkerRegistration, id:string|null) {
  const worker=reg.active;
  if(!worker) throw new Error('通知服务尚未准备好，请重试。');
  await bounded(new Promise<void>((resolve,reject)=>{
    const channel=new MessageChannel();
    channel.port1.onmessage=()=>{channel.port1.close();resolve();};
    channel.port1.onmessageerror=()=>{channel.port1.close();reject(new Error('通知状态保存失败'));};
    worker.postMessage({type:'PA_PUSH_BIND',id},[channel.port2]);
  }));
}

export async function clearLocalPush() {
  localStorage.removeItem(BINDING_KEY);
  if(!('serviceWorker' in navigator)) return;
  const reg=await navigator.serviceWorker.getRegistration('/');
  if(!reg?.active?.scriptURL.endsWith('/pa-push-sw.js')) return;
  await bind(reg,null);
  await (await reg.pushManager.getSubscription())?.unsubscribe();
}

export async function enablePush(publicKey:string) {
  const problem=pushSupport(); if(problem) throw new Error(problem);
  // Must be invoked from a user click, before any network await (Safari).
  if(await Notification.requestPermission()!=='granted') throw new Error('尚未授权通知。不会发送提醒，也不会影响其他功能。');
  await navigator.serviceWorker.register('/pa-push-sw.js',{scope:'/',updateViaCache:'none'});
  const reg=await bounded(navigator.serviceWorker.ready);
  const previous=await reg.pushManager.getSubscription();
  const previousId=localPushId();
  if(previousId) await request(`subscriptions/${encodeURIComponent(previousId)}`,'DELETE');
  await bind(reg,null); await previous?.unsubscribe();
  const decoded=atob(publicKey.replace(/-/g,'+').replace(/_/g,'/'));
  const key=new Uint8Array(Array.from(decoded,c=>c.charCodeAt(0)));
  let sub:PushSubscription|null=null;
  let newId:string|null=null;
  try {
    sub=await bounded(reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key}));
    const saved=await request('subscriptions','POST',sub.toJSON()); newId=saved.id;
    await bind(reg,saved.id); localStorage.setItem(BINDING_KEY,saved.id);
  } catch(error) {
    if(newId) await request(`subscriptions/${encodeURIComponent(newId)}`,'DELETE').catch(()=>{});
    await bind(reg,null).catch(()=>{}); await sub?.unsubscribe().catch(()=>{});
    localStorage.removeItem(BINDING_KEY); throw error;
  }
}

export async function disablePush(all=false) {
  const id=localPushId();
  // Stop local display even if the network is unavailable. Keep the opaque ID
  // until server revocation succeeds so a later click can retry that revocation.
  let localError=false;
  try {await clearLocalPush();} catch {localError=true;}
  try {
    if(all) await request('subscriptions','DELETE');
    else if(id) await request(`subscriptions/${encodeURIComponent(id)}`,'DELETE');
  } catch(error) { if(id)localStorage.setItem(BINDING_KEY,id); throw error; }
  if(localError) throw new Error('服务器已停止此订阅。浏览器状态未能清理，请在网站设置中关闭通知。');
}
