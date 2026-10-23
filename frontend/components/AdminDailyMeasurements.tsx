"use client";

import { useEffect, useRef, useState } from 'react';
import { MeasureSummary } from './DailyMeasureFields';
import { NotebookMark } from './icons';
import {
  fetchAdminMeasurementUsers, fetchAdminUserMeasurements,
  type AdminMeasurementUser, type AdminMeasurementUserPage, type AdminUserMeasurements,
} from '@/lib/adminAssessments';
import type { MeasurementRecord } from '@/lib/dailyMeasurement';
import { MEASURE_KEYS, type MeasureDraft } from '@/lib/dailyQuestionnaire';

const inputStyle = 'min-h-11 min-w-0 w-full rounded-xl border border-line-strong bg-raised/60 px-3 text-sm text-ink outline-none focus:border-accent-edge focus:depth-focus';
const buttonStyle = 'min-h-11 rounded-xl border border-line-strong px-4 text-sm text-ink-muted transition hover:bg-raised disabled:cursor-not-allowed disabled:opacity-40';
function userName(item: Pick<AdminMeasurementUser, 'nickname' | 'username' | 'subject_id' | 'research_id'>) {
  const name = item.nickname && item.username ? `${item.nickname}（${item.username}）` : item.nickname || item.username || item.subject_id;
  return item.research_id ? `${item.research_id} · ${name}` : name;
}
function errorMessage(error: unknown) { return error instanceof Error ? error.message : String(error); }
function scrollToPanel(panel: HTMLElement | null) {
  if (window.innerWidth < 1024) panel?.scrollIntoView({ block: 'start', behavior: 'instant' });
  panel?.focus({ preventScroll: true });
}
const toDraft = (record: MeasurementRecord): MeasureDraft =>
  Object.fromEntries(MEASURE_KEYS.map(k => [k, record.answers[k]])) as MeasureDraft;
const beijingTime = (iso: string) => new Date(iso).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });

/** 每日测量 tab of 每日记录数据: who filled today's questionnaire, and every answer they gave. */
export default function AdminDailyMeasurements({ refresh, onRefresh }: { refresh: number; onRefresh: () => void }) {
  const list = useRef<HTMLElement>(null), detail = useRef<HTMLElement>(null);
  const [draftQuery, setDraftQuery] = useState(''), [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<AdminMeasurementUserPage | null>(null);
  const [selected, setSelected] = useState<AdminMeasurementUser | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState('');

  useEffect(() => {
    const control = new AbortController();
    setLoading(true); setError('');
    void fetchAdminMeasurementUsers(query, offset, control.signal)
      .then(result => {
        if (control.signal.aborted) return;
        setData(result);
        setSelected(current => current ? result.items.find(item => item.subject_id === current.subject_id) ?? current : null);
      })
      .catch(err => { if (!control.signal.aborted) setError(errorMessage(err)); })
      .finally(() => { if (!control.signal.aborted) setLoading(false); });
    return () => control.abort();
  }, [query, offset, refresh]);

  function applyQuery() { setQuery(draftQuery.trim()); setOffset(0); setData(null); setSelected(null); }
  function page(nextOffset: number) { setData(null); setOffset(nextOffset); }

  return <>
    <div className="flex flex-wrap items-end justify-between gap-3 rounded-2xl bg-raised/60 p-4">
      <form onSubmit={event => { event.preventDefault(); applyQuery(); }} className="flex w-full flex-wrap items-end gap-2 sm:w-auto sm:flex-1">
        <label className="min-w-0 flex-1 sm:max-w-md"><span className="mb-1.5 block text-xs text-ink-muted">查找被试</span><input aria-label="查找被试" placeholder="被试编号、账号或昵称" maxLength={64} value={draftQuery} onChange={event => setDraftQuery(event.target.value)} className={inputStyle} /></label>
        <button type="submit" className="min-h-11 rounded-xl bg-accent px-4 text-sm font-medium text-on-accent">查找</button>
        {query && <button type="button" className={buttonStyle} onClick={() => { setDraftQuery(''); setQuery(''); setOffset(0); setData(null); setSelected(null); }}>清除</button>}
      </form>
      <button aria-label="刷新每日测量填写状态" className={buttonStyle} disabled={loading} onClick={onRefresh}>{loading && data ? '正在刷新…' : '刷新'}</button>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-2 py-4 text-xs text-ink-muted">
      <p role="status">{data ? <>今日 {data.today}（北京时间）已填写 <strong className="text-base font-semibold tabular-nums text-ink">{data.today_count}</strong> / {data.total} 位{query ? '符合条件的' : ''}被试</> : loading ? '正在读取被试…' : '被试暂未载入'}</p>
      <p>按测量日期判断；补填昨天不算今日填写。</p>
    </div>
    {error && <p role="alert" className="mb-4 rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}<button className="ml-2 min-h-11 underline" onClick={onRefresh}>重试读取</button></p>}
    <div className="grid min-w-0 items-start gap-5 lg:grid-cols-[minmax(250px,0.8fr)_minmax(0,2fr)]">
      <section ref={list} tabIndex={-1} aria-label="被试每日测量列表" className="min-w-0 outline-none">
        <div className="overflow-hidden rounded-2xl border border-line">
          <h3 className="border-b border-line bg-raised/60 px-4 py-3 text-sm font-medium">被试与今日测量状态</h3>
          <ul className="zen-scroll max-h-80 divide-y divide-line overflow-y-auto lg:max-h-[calc(100dvh-370px)] lg:min-h-48">
            {data?.items.map(item => <li key={item.subject_id}>
              <button aria-label={`查看 ${userName(item)} 的每日测量`} aria-pressed={selected?.subject_id === item.subject_id} onClick={() => { setSelected(item); requestAnimationFrame(() => scrollToPanel(detail.current)); }} className={`min-h-20 w-full px-4 py-3 text-left transition focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent ${selected?.subject_id === item.subject_id ? 'bg-accent-wash' : 'hover:bg-raised/60'}`}>
                <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className="min-w-0 break-words text-sm font-medium">{userName(item)}</span>
                  <span className={`shrink-0 text-xs font-medium ${item.today_completed ? 'text-[#8dc3b1] [.theme-warm_&]:text-[#456f60]' : 'text-alert-ink'}`}>{item.today_completed ? '今日已填写' : '今日未填写'}</span>
                </span>
                <span className="mt-1.5 block text-xs leading-5 text-ink-muted">{item.day_count ? `共填写 ${item.day_count} 天` : '还没有填写过每日测量'}{item.last_date && <span className="block tabular-nums">最近测量日期：{item.last_date}</span>}</span>
              </button>
            </li>)}
          </ul>
          {loading && !data && <p className="p-8 text-center text-sm text-ink-muted">正在加载…</p>}
          {!loading && data?.items.length === 0 && <p className="p-8 text-center text-sm leading-6 text-ink-muted">{query ? '没有找到符合条件的被试。' : '暂无被试。'}</p>}
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-ink-muted">每页 20 人 · 第 {Math.floor(offset / 20) + 1} 页</span>
          <div className="flex gap-2"><button className={buttonStyle} disabled={!offset || loading} onClick={() => page(Math.max(0, offset - 20))}>上一页</button><button className={buttonStyle} disabled={!data?.has_more || loading} onClick={() => page(offset + 20)}>下一页</button></div>
        </div>
      </section>
      <section ref={detail} tabIndex={-1} aria-label="被试每日测量详情" className="min-w-0 outline-none">
        {selected ? <UserMeasurements key={selected.subject_id} user={selected} refresh={refresh} onBack={() => scrollToPanel(list.current)} /> : <div className="flex min-h-72 flex-col items-center justify-center rounded-2xl border border-dashed border-line px-6 text-center"><NotebookMark className="mb-3 h-7 w-7 text-accent-ink" /><h3 className="font-medium">先选择一位被试</h3><p className="mt-2 max-w-xs text-sm leading-6 text-ink-muted">查看这位被试填过的每一天，再选择一天查看全部答案。</p></div>}
      </section>
    </div>
  </>;
}

function UserMeasurements({ user, refresh, onBack }: { user: AdminMeasurementUser; refresh: number; onBack: () => void }) {
  const datesPanel = useRef<HTMLElement>(null), dayPanel = useRef<HTMLElement>(null);
  const [data, setData] = useState<AdminUserMeasurements | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  useEffect(() => {
    const control = new AbortController(); setLoading(true); setError('');
    void fetchAdminUserMeasurements(user.subject_id, control.signal)
      .then(result => { if (!control.signal.aborted) setData(result); })
      .catch(err => { if (!control.signal.aborted) setError(errorMessage(err)); })
      .finally(() => { if (!control.signal.aborted) setLoading(false); });
    return () => control.abort();
  }, [user.subject_id, refresh, retry]);
  const record = data?.items.find(item => item.id === selectedId) ?? null;
  return <>
    <button className="mb-2 min-h-11 rounded-xl px-3 text-sm text-accent-ink hover:bg-raised lg:hidden" onClick={onBack}>返回被试列表</button>
    <div className="mb-4"><h3 className="break-words text-base font-semibold">{userName(user)}</h3><p className="mt-1 break-all text-xs leading-5 text-ink-muted">被试编号：{user.research_id ?? '未填写'} · 账号：{user.username ?? '—'}</p></div>
    <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(180px,0.65fr)_minmax(0,1.5fr)]">
      <section ref={datesPanel} tabIndex={-1} aria-label="测量日期列表" className="min-w-0 rounded-2xl border border-line outline-none">
        <h4 className="border-b border-line bg-raised/60 px-4 py-3 text-sm font-medium">测量日期{data && <span className="ml-2 text-xs font-normal text-ink-muted">共 {data.total} 天</span>}</h4>
        {error && <p role="alert" className="p-4 text-sm text-alert-ink">{error}<button className="mt-1 block min-h-11 underline" onClick={() => setRetry(value => value + 1)}>重试读取</button></p>}
        {loading && !data && <p className="p-6 text-sm text-ink-muted">正在读取…</p>}
        {data?.items.length === 0 && <p className="p-6 text-sm leading-6 text-ink-muted">该被试还没有提交过每日测量。</p>}
        <ul className="zen-scroll max-h-80 divide-y divide-line overflow-y-auto xl:max-h-[calc(100dvh-350px)]">
          {data?.items.map(item => <li key={item.id}><button aria-label={`查看 ${item.local_date} 的每日测量`} aria-pressed={selectedId === item.id} onClick={() => { setSelectedId(item.id); requestAnimationFrame(() => scrollToPanel(dayPanel.current)); }} className={`min-h-16 w-full px-4 py-3 text-left transition focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent ${selectedId === item.id ? 'bg-accent-wash' : 'hover:bg-raised/60'}`}><span className="block text-sm font-medium tabular-nums">{item.local_date}</span><span className="mt-1 block text-xs text-ink-muted">{item.revision_no > 1 ? `修改过 ${item.revision_no - 1} 次` : '未修改'}</span></button></li>)}
        </ul>
      </section>
      <section ref={dayPanel} tabIndex={-1} aria-label="当日每日测量" className="min-w-0 outline-none">
        {record ? <>
          <button className="mb-2 min-h-11 rounded-xl px-3 text-sm text-accent-ink hover:bg-raised xl:hidden" onClick={() => scrollToPanel(datesPanel.current)}>返回日期列表</button>
          <h4 className="font-medium tabular-nums">{record.local_date}</h4>
          <p className="mb-3 mt-1 text-xs leading-5 text-ink-muted">提交于 {beijingTime(record.submitted_at)}{record.revision_no > 1 && ` · 最后修改于 ${beijingTime(record.updated_at)}（第 ${record.revision_no} 版）`} · 北京时间</p>
          <MeasureSummary answers={toDraft(record)} />
        </> : <div className="flex min-h-48 flex-col items-center justify-center rounded-2xl bg-raised/40 px-5 text-center"><p className="text-sm font-medium">选择一个测量日期</p><p className="mt-2 text-xs leading-6 text-ink-muted">展示这一天每日测量的全部答案。</p></div>}
      </section>
    </div>
  </>;
}
