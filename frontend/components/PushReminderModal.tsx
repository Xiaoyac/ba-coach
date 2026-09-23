"use client";
import { useEffect, useRef, useState } from 'react';
import { readPushStatus, pushSupport, localPushId, enablePush, disablePush, readPushChecks, schedulePushCheck, pushCheckMessage, type PushStatus, type PushCheck } from '@/lib/push';

export default function PushReminderModal({onClose}:{onClose:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [status,setStatus]=useState<PushStatus|null>(null),[error,setError]=useState('');
  const [busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[enabled,setEnabled]=useState(false);
  const [support,setSupport]=useState<string|null>(null),[notice,setNotice]=useState('');
  const [checks,setChecks]=useState<PushCheck[]>([]);
  const [delay,setDelay]=useState('5');
  const delaySeconds=Number(delay),validDelay=Number.isInteger(delaySeconds)&&delaySeconds>=1&&delaySeconds<=600;
  const latestCheck=checks.find(c=>c.device_id===localPushId());
  async function load() {
    setLoading(true);setError('');setSupport(pushSupport());
    try { const data=await readPushStatus();setStatus(data);
      setEnabled(Boolean(data.devices?.some(d=>d.id===localPushId()&&d.this_session))&&typeof Notification!=='undefined'&&Notification.permission==='granted');
      setChecks(data.available?(await readPushChecks()).items??[]:[]);
    } catch(e) {setError(e instanceof Error?e.message:'加载失败，请重试。');}
    finally {setLoading(false);}
  }
  useEffect(()=>{dialog.current?.showModal();void load();},[]);
  useEffect(()=>{
    if(!latestCheck||latestCheck.displayed_at||!['queued','attempting','accepted','unknown','timeout','unreachable'].includes(latestCheck.state)
      ||new Date(latestCheck.expires_at).getTime()<=Date.now()) return;
    let stopped=false,fetching=false;
    const timer=setInterval(()=>{
      if(new Date(latestCheck.expires_at).getTime()<=Date.now()) {clearInterval(timer);return;}
      if(document.visibilityState!=='visible'||fetching) return;
      fetching=true;
      void readPushChecks().then(data=>{if(!stopped)setChecks(data.items??[]);}).catch(()=>{}).finally(()=>{fetching=false;});
    },3000);
    return ()=>{stopped=true;clearInterval(timer);};
  },[latestCheck?.id,latestCheck?.state,latestCheck?.displayed_at,latestCheck?.expires_at]);
  async function testClosedPage() {
    setBusy(true);setError('');setNotice('');
    try {const check=await schedulePushCheck(delaySeconds);setChecks(previous=>[check,...previous.filter(c=>c.id!==check.id)]);}
    catch(e) {setError(e instanceof Error?e.message:'预约测试失败，请重试。');}
    finally {setBusy(false);}
  }
  async function change(action:'enable'|'disable'|'all') {
    setBusy(true);setError('');setNotice('');
    try {
      if(action==='enable') await enablePush(status!.public_key!);else await disablePush(action==='all');
      await load();setNotice(action==='enable'?'已开启。仅对时间明确、尚未反馈的活动发送一次提醒。':'已关闭提醒。不会影响目标或历史记录。');
    } catch(e) {setError(e instanceof Error?e.message:'操作失败，请重试。');setSupport(pushSupport());}
    finally {setBusy(false);}
  }
  return <dialog ref={dialog} onClose={onClose} aria-labelledby="pa-push-title" className="m-auto w-[min(520px,calc(100vw-24px))] max-h-[calc(100dvh-32px)] overflow-y-auto rounded-3xl border-0 bg-sheet p-6 text-ink shadow-2xl backdrop:bg-black/40 sm:p-8">
    <header className="flex items-start justify-between gap-4"><div><p className="text-xs text-accent-ink">PA · 轻一点的提醒</p><h2 id="pa-push-title" className="mt-2 text-2xl font-semibold">活动后提醒</h2></div><button onClick={onClose} aria-label="关闭提醒设置" className="surface-button min-h-11 min-w-11 rounded-full text-xl">×</button></header>
    <p className="mt-5 text-sm leading-7 text-ink-muted">预计活动结束 <strong className="font-medium text-ink">15 分钟后</strong>，如果还没有收到反馈，轻轻提醒一次。做了、没做或有变化，都可以回来聊聊。</p>
    <div className="mt-5 rounded-2xl bg-raised/70 p-4 text-sm leading-6"><p className="font-medium">只有时间明确，才会安排</p><p className="mt-1 text-ink-muted">没有明确日期、开始时间或时长的计划不会提醒。已反馈、取消、暂停或改期后，会重新检查，不连续催促。</p></div>
    {loading?<p role="status" className="mt-5 text-sm">正在读取设置…</p>:<>
      {!status?.available&&<p className="mt-5 text-sm text-ink-muted">推送服务尚未启用，当前不会发送通知。</p>}
      {support&&<p className="mt-5 text-sm leading-6 text-ink-muted">{support}</p>}
      {status?.preference_blocked&&<p className="mt-4 text-sm text-ink-muted">你的档案选择了不主动提醒，目前不会发送。若想开启，请先在「我的档案」调整提醒偏好。</p>}
      <div className="mt-5 flex flex-wrap gap-3">
        {enabled?<button disabled={busy} onClick={()=>void change('disable')} className="surface-button min-h-11 rounded-xl bg-raised px-4 text-sm">关闭此浏览器提醒</button>:<button disabled={busy||!status?.available||!!support||status?.preference_blocked} onClick={()=>void change('enable')} className="surface-button min-h-11 rounded-xl bg-accent-wash px-4 text-sm font-medium text-accent-ink disabled:opacity-50">{busy?'正在连接…':'在此浏览器开启提醒'}</button>}
        {!!status?.devices?.length&&<button disabled={busy} onClick={()=>void change('all')} className="surface-button min-h-11 rounded-xl px-3 text-xs text-ink-muted">关闭所有设备提醒</button>}
      </div>
      {enabled&&<p className="mt-3 text-sm text-accent-ink">此浏览器已开启 · 已授权 {status?.devices.length??0} 个设备订阅</p>}
      {(enabled||latestCheck)&&<section aria-label="关页接收测试" className="mt-5 rounded-2xl bg-raised/70 p-4 text-sm leading-6">
        <h3 className="font-medium">试试关掉网页后接收</h3>
        <p className="mt-1 text-ink-muted">选择多久后发送，默认 5 秒。到时由服务器发送，实际到达受网络和浏览器影响。无需修改目标，也不需要一直开着本页。</p>
        <label className="mt-3 flex flex-wrap items-center gap-2">测试延迟
          <input aria-label="测试延迟（秒）" type="number" min="1" max="600" step="1" value={delay} disabled={busy}
            onChange={event=>setDelay(event.target.value)} className="min-h-11 w-24 rounded-xl border border-current/20 bg-sheet px-3 text-ink" />
          <span className="text-ink-muted">秒（1–600）</span>
        </label>
        {!validDelay&&<p className="mt-1 text-alert-ink">请输入 1–600 秒的整数。</p>}
        <div className="mt-3 flex flex-wrap gap-2">
          <button disabled={busy||!enabled||!validDelay||status?.preference_blocked} onClick={()=>void testClosedPage()} className="surface-button min-h-11 rounded-xl bg-accent-wash px-3 text-sm font-medium text-accent-ink disabled:opacity-50">{busy?'正在预约…':'发送测试通知'}</button>
          <button disabled={busy} onClick={()=>void load()} className="surface-button min-h-11 rounded-xl px-3 text-sm text-ink-muted">查看测试结果</button>
        </div>
        {latestCheck&&<p role="status" className="mt-3 text-ink-muted">{pushCheckMessage(latestCheck)}</p>}
        <p className="mt-2 text-xs text-ink-muted">显示确认不代表你已经看到通知。关闭网页不等于强制结束浏览器；电脑可能需允许浏览器后台运行，手机需允许通知并避免强行停止应用。</p>
      </section>}
      {enabled&&<div className="mt-5 text-sm"><p className="font-medium">已识别的近期提醒时间</p>{status?.upcoming?.length?<ul className="mt-2 space-y-2 text-ink-muted">{status.upcoming.slice(0,5).map((item,i)=><li key={`${item.goal_id}-${i}`}>{new Date(item.due_at).toLocaleString('zh-CN')} · 发送前仍会检查反馈</li>)}</ul>:<p className="mt-2 text-ink-muted">暂时没有可确定时间的安排。可在目标对话中明确日期、开始时间和时长，无需另外填表。</p>}</div>}
    </>}
    {error&&<div role="alert" className="mt-4 text-sm text-alert-ink"><p>{error}</p><button disabled={busy} onClick={()=>void load()} className="mt-2 min-h-11 underline">重新读取设置</button></div>}
    {notice&&<p role="status" className="mt-4 text-sm text-accent-ink">{notice}</p>}
    <p className="mt-5 text-xs leading-6 text-ink-muted">遵循档案中的提醒频率与时段，过时不补发。关闭网页后也可接收，但受网络、浏览器及省电设置影响，不保证准时到达。大陆网络下 Chrome 等浏览器依赖的 Google 推送服务可能不可达，可尝试 Firefox 或 iPhone 主屏幕应用。不展示目标名称或聊天内容；开启后会向浏览器推送服务发送加密通知。退出登录后停止该登录状态的提醒。可随时关闭。</p>
  </dialog>;
}
