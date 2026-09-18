"use client";

import { useEffect, useRef, useState } from 'react';
import AssessmentHistory from './AssessmentHistory';
import WorkbenchSelect from './WorkbenchSelect';
import { CloseMark, NotebookMark } from './icons';
import { exportAdminRecords, fetchAdminRecords, type AdminRecord, type AdminRecordPage, type RecordFilters } from '@/lib/adminAssessments';

const empty: RecordFilters = {query:'',start:'',end:'',scale_version:''};
const inputStyle='min-h-11 min-w-0 w-full rounded-xl border border-line-strong bg-raised/60 px-3 text-sm text-ink outline-none focus:border-accent-edge focus:ring-2 focus:ring-accent-wash';
const buttonStyle='min-h-11 rounded-xl border border-line-strong px-4 text-sm text-ink-muted transition hover:bg-raised disabled:cursor-not-allowed disabled:opacity-40';
function name(item: AdminRecord) { return item.display_id || item.username || '已注销账号'; }
function score(value: number | null, maximum: number) { return value == null ? '未填写' : `${value}/${maximum}`; }

export default function AdminDailyRecords({onClose}:{onClose:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null), detail=useRef<HTMLElement>(null), list=useRef<HTMLElement>(null);
  const [draft,setDraft]=useState<RecordFilters>(empty),[filters,setFilters]=useState<RecordFilters>(empty);
  const [offset,setOffset]=useState(0),[revision,setRevision]=useState(0);
  const [data,setData]=useState<AdminRecordPage|null>(null),[selected,setSelected]=useState<AdminRecord|null>(null);
  const [loading,setLoading]=useState(true),[error,setError]=useState(''),[exporting,setExporting]=useState(false),[notice,setNotice]=useState('');
  const exportControl=useRef<AbortController|null>(null);
  const dirty=JSON.stringify(draft)!==JSON.stringify(filters);
  useEffect(()=>{dialog.current?.showModal();return ()=>exportControl.current?.abort();},[]);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError('');setData(null);setSelected(null);setNotice('');
    void fetchAdminRecords(filters,offset,controller.signal).then(result=>{if(!controller.signal.aborted)setData(result);})
      .catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:String(e));})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[filters,offset,revision]);
  function apply() {
    if(draft.start && draft.end && draft.start>draft.end) {setError('开始日期不能晚于结束日期');return;}
    setFilters({...draft});setOffset(0);
  }
  async function download(kind:'daily'|'activities') {
    if(exporting || !data || loading || dirty)return;
    const controller=new AbortController();exportControl.current=controller;setExporting(true);setError('');setNotice('');
    try{await exportAdminRecords(filters,kind,controller.signal);if(!controller.signal.aborted)setNotice('已生成下载文件，请妥善保存用户资料。');}
    catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:String(e));}
    finally{if(!controller.signal.aborted)setExporting(false);}
  }
  function choose(item:AdminRecord) {
    setSelected(item);
    requestAnimationFrame(()=>{if(window.innerWidth<1024)detail.current?.scrollIntoView({block:'start',behavior:'instant'});detail.current?.focus({preventScroll:true});});
  }
  return <dialog ref={dialog} onClose={onClose} aria-labelledby="admin-daily-title" className="m-auto h-[min(900px,calc(100dvh-32px))] max-h-none w-[min(1400px,calc(100vw-32px))] max-w-none overflow-hidden rounded-3xl border-0 bg-sheet p-0 text-ink shadow-2xl backdrop:bg-black/40 max-sm:h-[100dvh] max-sm:w-screen max-sm:rounded-none">
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-start justify-between gap-3 px-5 pb-4 pt-5 sm:px-7">
        <div><p className="mb-1 flex items-center gap-2 text-xs text-accent-ink"><NotebookMark className="h-4 w-4"/>管理员 · 数据查阅</p><h2 id="admin-daily-title" className="text-xl font-semibold sm:text-2xl">每日记录数据</h2><p className="mt-1 text-xs leading-5 text-ink-muted">查看用户已提交的记录，不修改用户原始填写。</p></div>
        <button onClick={onClose} aria-label="关闭每日记录数据" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full hover:bg-raised"><CloseMark className="h-4 w-4"/></button>
      </header>
      <div className="zen-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-6 sm:px-7">
        <form onSubmit={e=>{e.preventDefault();apply();}} className="grid grid-cols-2 items-end gap-3 rounded-2xl bg-raised/60 p-4 lg:grid-cols-[minmax(180px,1.5fr)_1fr_1fr_1fr_auto]">
          <label className="col-span-2 min-w-0 lg:col-span-1"><span className="mb-1.5 block text-xs text-ink-muted">用户</span><input aria-label="查找用户" placeholder="账号、昵称#标签或用户编号" maxLength={64} value={draft.query} onChange={e=>setDraft({...draft,query:e.target.value})} className={inputStyle}/></label>
          <label className="min-w-0"><span className="mb-1.5 block text-xs text-ink-muted">开始日期</span><input type="date" aria-label="开始日期" value={draft.start} onChange={e=>setDraft({...draft,start:e.target.value})} className={inputStyle}/></label>
          <label className="min-w-0"><span className="mb-1.5 block text-xs text-ink-muted">结束日期</span><input type="date" aria-label="结束日期" value={draft.end} onChange={e=>setDraft({...draft,end:e.target.value})} className={inputStyle}/></label>
          <label className="min-w-0"><span className="mb-1.5 block text-xs text-ink-muted">量表版本</span><WorkbenchSelect aria-label="量表版本" className="w-full" value={draft.scale_version} onChange={e=>setDraft({...draft,scale_version:e.target.value})}><option value="">全部版本</option><option value="2">新版 · 0–5</option><option value="1">旧版 · 0–10</option></WorkbenchSelect></label>
          <div className="flex gap-2"><button type="submit" disabled={exporting} className="min-h-11 flex-1 rounded-xl bg-accent px-4 text-sm font-medium text-on-accent disabled:opacity-40">查询</button><button type="button" disabled={exporting} className={buttonStyle} onClick={()=>{setDraft(empty);setFilters({...empty});setOffset(0);}}>重置</button></div>
        </form>
        <div className="flex flex-wrap items-center justify-between gap-3 py-4">
          <p role="status" className="text-sm text-ink-muted">{loading?'正在读取记录…':data?<><strong className="text-lg font-semibold tabular-nums text-ink">{data.total}</strong> 份记录 <span className="mx-2 text-line-strong">/</span> {data.users} 位用户 <span className="ml-2 text-xs">新版 {data.versions['2']??0} · 旧版 {data.versions['1']??0}</span></>:'暂未载入'}<span className="mt-1 block text-xs">{dirty?'筛选条件已修改，请点击查询后导出。':'统计与导出遵循当前筛选，日期按用户记录当天计算。'}</span></p>
          <div className="flex flex-wrap gap-2"><button disabled={exporting||loading} className={buttonStyle} onClick={()=>setRevision(v=>v+1)}>刷新</button><button disabled={exporting||loading||dirty||!data?.total} className={buttonStyle} onClick={()=>void download('daily')}>{exporting?'正在导出…':'导出每日汇总'}</button><button disabled={exporting||loading||dirty||!data?.total} className={buttonStyle} onClick={()=>void download('activities')}>导出活动明细</button></div>
        </div>
        {error&&<p role="alert" className="mb-4 rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}</p>}
        {notice&&<p role="status" className="mb-4 rounded-xl bg-accent-wash p-3 text-sm text-accent-ink">{notice}</p>}
        <div className="grid min-w-0 items-start gap-5 lg:grid-cols-[minmax(0,1.45fr)_minmax(300px,1fr)]">
          <section ref={list} aria-label="每日记录列表" className="min-w-0">
            <div className="overflow-hidden rounded-2xl border border-line">
              <table className="w-full table-fixed text-left text-sm"><thead className="bg-raised/60 text-xs text-ink-muted"><tr><th className="w-[44%] px-4 py-3 font-medium">用户 / 日期</th><th className="px-3 py-3 font-medium">完成 · 活动 · 心情</th><th className="hidden w-16 px-2 py-3 font-medium sm:table-cell">活动数</th></tr></thead>
                <tbody className="divide-y divide-line">{data?.items.map(item=>{const r=item.record,max=r.scale_version===2?5:10;return <tr key={r.id} className={selected?.record.id===r.id?'bg-accent-wash':'hover:bg-raised/40'}><td className="px-4 py-3"><button aria-label={`查看 ${name(item)} ${r.local_date}`} onClick={()=>choose(item)} className="min-h-11 w-full rounded-lg text-left focus-visible:outline-2 focus-visible:outline-accent"><span className="block break-words font-medium text-ink">{name(item)}</span><span className="mt-1 block text-xs tabular-nums text-ink-muted">{r.local_date}</span></button></td><td className="px-3 py-3"><span className="block break-words text-xs leading-6 tabular-nums sm:text-sm">{r.completion_not_applicable?'不适用':score(r.completion_rate,max)} · {score(r.activity_level,max)} · {score(r.overall_mood,max)}</span><span className="text-xs text-ink-muted">{r.scale_version===2?'新版量表':'旧版量表'} · 点用户查看</span></td><td className="hidden px-2 py-3 tabular-nums text-ink-muted sm:table-cell">{r.activities.length}</td></tr>;})}</tbody>
              </table>
              {loading&&<p className="p-8 text-center text-sm text-ink-muted">正在加载…</p>}
              {!loading&&data?.items.length===0&&<p className="p-8 text-center text-sm leading-6 text-ink-muted">没有符合筛选条件的记录。<br/>可调整用户、日期或量表版本。</p>}
              {!loading&&!data&&<button className="m-4 min-h-11 rounded-xl px-4 text-sm text-accent-ink hover:bg-raised" onClick={()=>setRevision(v=>v+1)}>重新加载</button>}
            </div>
            <div className="mt-3 flex items-center justify-between gap-2"><span className="text-xs text-ink-muted">每页 20 份 · 第 {Math.floor(offset/20)+1} 页</span><div className="flex gap-2"><button className={buttonStyle} disabled={!offset||loading||exporting} onClick={()=>setOffset(v=>Math.max(0,v-20))}>上一页</button><button className={buttonStyle} disabled={!data?.has_more||loading||exporting} onClick={()=>setOffset(v=>v+20)}>下一页</button></div></div>
          </section>
          <aside ref={detail} tabIndex={-1} aria-label="记录详情" className="min-h-[calc(100dvh-140px)] min-w-0 rounded-2xl bg-raised/35 p-4 outline-none lg:sticky lg:top-0 lg:max-h-[calc(100dvh-160px)] lg:min-h-0 lg:overflow-y-auto">
            {selected&&<button className="mb-3 min-h-11 rounded-xl px-3 text-sm text-accent-ink hover:bg-raised lg:hidden" onClick={()=>list.current?.scrollIntoView({block:'start',behavior:'instant'})}>返回记录列表</button>}
            {selected?<><div className="mb-4"><h3 className="break-words text-base font-semibold">{name(selected)}</h3><p className="mt-1 break-all text-xs text-ink-muted">账号：{selected.username??'已注销'}<br/>用户编号：{selected.subject_id}</p></div><AssessmentHistory key={selected.record.id} records={[selected.record]} loading={false} loadingMore={false} error={null} hasMore={false} onLoadMore={()=>{}} onRetry={()=>{}}/></>:<div className="flex min-h-64 flex-col items-center justify-center px-4 text-center"><NotebookMark className="mb-3 h-7 w-7 text-accent-ink"/><h3 className="font-medium">把一天的记录放在一起看</h3><p className="mt-2 max-w-60 text-sm leading-6 text-ink-muted">选择左侧用户记录，查看活动时间、内容、心情和选填感受。</p></div>}
          </aside>
        </div>
        <p className="mt-5 text-xs leading-6 text-ink-muted">仅显示已提交记录，未填写不等于 0 分。旧版 0–10 与新版 0–5 不混算。CSV 保留空值与“不适用”标记，每次最多导出 1000 份记录；活动明细一项活动一行，请勿重复累计其中的每日评分。文件含用户资料，请仅用于授权的数据收集。</p>
      </div>
    </div>
  </dialog>;
}
