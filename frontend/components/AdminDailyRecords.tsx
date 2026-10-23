"use client";

import { useEffect, useRef, useState } from 'react';
import AssessmentHistory from './AssessmentHistory';
import AdminDailyMeasurements from './AdminDailyMeasurements';
import WorkbenchSelect from './WorkbenchSelect';
import { CloseMark, NotebookMark } from './icons';
import {
  exportAdminRecords, fetchAdminRecords,
  fetchAdminRecordUsers, fetchAdminRecordDates, fetchAdminRecordDay,
  type AdminRecord, type AdminRecordPage, type RecordFilters, type ExportKind,
  type AdminRecordUser, type AdminRecordUserPage, type AdminRecordDates, type AdminRecordDay,
} from '@/lib/adminAssessments';

const empty: RecordFilters = { query: '', start: '', end: '', scale_version: '' };
const inputStyle = 'min-h-11 min-w-0 w-full rounded-xl border border-line-strong bg-raised/60 px-3 text-sm text-ink outline-none focus:border-accent-edge focus:depth-focus';
const buttonStyle = 'min-h-11 rounded-xl border border-line-strong px-4 text-sm text-ink-muted transition hover:bg-raised disabled:cursor-not-allowed disabled:opacity-40';
function userName(item: Pick<AdminRecordUser, 'nickname' | 'username' | 'subject_id'>) {
  return item.nickname && item.username
    ? `${item.nickname}（${item.username}）`
    : item.nickname || item.username || item.subject_id;
}
function errorMessage(error: unknown) { return error instanceof Error ? error.message : String(error); }
function scrollToPanel(panel: HTMLElement | null) {
  if (window.innerWidth < 1024) panel?.scrollIntoView({ block: 'start', behavior: 'instant' });
  panel?.focus({ preventScroll: true });
}

export default function AdminDailyRecords({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), list = useRef<HTMLElement>(null), dates = useRef<HTMLElement>(null);
  const [draftQuery, setDraftQuery] = useState(''), [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0), [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<AdminRecordUserPage | null>(null);
  const [selected, setSelected] = useState<AdminRecordUser | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState('');
  const [exportOpen, setExportOpen] = useState(false);
  const [tab, setTab] = useState<'records' | 'measurements'>('records');

  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    // The server owns the Beijing calendar date. Refresh on return and while
    // open so a dialog left open across midnight does not retain yesterday's status.
    const update = () => setRefresh(value => value + 1);
    const visible = () => { if (document.visibilityState === 'visible') update(); };
    const timer = window.setInterval(visible, 60_000);
    window.addEventListener('focus', update);
    document.addEventListener('visibilitychange', visible);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', update);
      document.removeEventListener('visibilitychange', visible);
    };
  }, []);
  useEffect(() => {
    const control = new AbortController();
    setLoading(true); setError('');
    void fetchAdminRecordUsers(query, offset, control.signal)
      .then(result => {
        if (control.signal.aborted) return;
        setData(result);
        setSelected(current => current ? result.items.find(item => item.subject_id === current.subject_id) ?? current : null);
      })
      .catch(err => { if (!control.signal.aborted) setError(errorMessage(err)); })
      .finally(() => { if (!control.signal.aborted) setLoading(false); });
    return () => control.abort();
  }, [query, offset, refresh]);

  function applyQuery() {
    setQuery(draftQuery.trim()); setOffset(0); setData(null); setSelected(null); setRefresh(value => value + 1);
  }
  function chooseUser(item: AdminRecordUser) {
    setSelected(item);
    requestAnimationFrame(() => scrollToPanel(dates.current));
  }
  function page(nextOffset: number) { setData(null); setOffset(nextOffset); }

  return <dialog ref={dialog} onClose={onClose} aria-labelledby="admin-daily-title" className="default-style:m-auto default-style:h-[min(900px,calc(100dvh-32px))] default-style:max-h-none default-style:w-[min(1480px,calc(100vw-32px))] default-style:max-w-none default-style:overflow-hidden default-style:rounded-3xl default-style:border-0 default-style:bg-sheet default-style:p-0 default-style:text-ink default-style:shadow-2xl default-style:backdrop:bg-black/40 default-style:max-sm:h-[100dvh] default-style:max-sm:w-screen default-style:max-sm:rounded-none simple:m-auto simple:h-[min(900px,calc(100dvh-32px))] simple:max-h-none simple:w-[min(1480px,calc(100vw-32px))] simple:max-w-none simple:overflow-hidden simple:rounded-2xl simple:border-0 simple:bg-sheet simple:p-0 simple:text-ink simple:depth-panel simple:backdrop:bg-black/40 simple:max-sm:h-[100dvh] simple:max-sm:w-screen simple:max-sm:rounded-none">
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-start justify-between gap-3 px-5 pb-4 pt-5 sm:px-7">
        <div>
          <p className="mb-1 flex items-center gap-2 text-xs text-accent-ink"><NotebookMark className="h-4 w-4" />管理员 · 数据查阅</p>
          <h2 id="admin-daily-title" className="text-xl font-semibold sm:text-2xl">每日记录数据</h2>
          <p className="mt-1 text-xs leading-5 text-ink-muted">按用户查看每日记录和每日测量的填写情况。仅统计普通用户，不含管理员。</p>
        </div>
        <button onClick={onClose} aria-label="关闭每日记录数据" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full hover:bg-raised"><CloseMark className="h-4 w-4" /></button>
      </header>
      <div role="tablist" aria-label="数据类型" className="flex shrink-0 gap-1 px-5 pb-3 sm:px-7">
        {([['records', '每日记录'], ['measurements', '每日测量']] as const).map(([value, label]) =>
          <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)}
            className={`min-h-11 rounded-full px-4 text-sm transition ${tab === value ? 'bg-accent-wash font-medium text-accent-ink' : 'text-ink-muted hover:bg-raised'}`}>{label}</button>)}
      </div>
      <div className="zen-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-6 sm:px-7">
        {tab === 'measurements' ? <AdminDailyMeasurements refresh={refresh} onRefresh={() => setRefresh(value => value + 1)} /> : <>
        <div className="flex flex-wrap items-end justify-between gap-3 rounded-2xl bg-raised/60 p-4">
          <form onSubmit={event => { event.preventDefault(); applyQuery(); }} className="flex w-full flex-wrap items-end gap-2 sm:w-auto sm:flex-1">
            <label className="min-w-0 flex-1 sm:max-w-md"><span className="mb-1.5 block text-xs text-ink-muted">查找用户</span><input aria-label="查找用户" placeholder="账号、昵称或用户编号" maxLength={64} value={draftQuery} onChange={event => setDraftQuery(event.target.value)} className={inputStyle} /></label>
            <button type="submit" className="min-h-11 rounded-xl bg-accent px-4 text-sm font-medium text-on-accent">查找</button>
            {query && <button type="button" className={buttonStyle} onClick={() => { setDraftQuery(''); setQuery(''); setOffset(0); setData(null); setSelected(null); }}>清除</button>}
          </form>
          <button aria-label="刷新用户填写状态" className={buttonStyle} disabled={loading} onClick={() => setRefresh(value => value + 1)}>{loading && data ? '正在刷新…' : '刷新'}</button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 py-4 text-xs text-ink-muted">
          <p role="status">{data ? <><strong className="text-base font-semibold tabular-nums text-ink">{data.total}</strong> 位普通用户 · 今日 {data.today}（北京时间）</> : loading ? '正在读取用户…' : '用户暂未载入'}</p>
          <p>按记录日期判断；补填昨天不算今日填写。</p>
        </div>
        {error && <p role="alert" className="mb-4 rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}<button className="ml-2 min-h-11 underline" onClick={() => setRefresh(value => value + 1)}>重试读取用户</button></p>}
        <div className="grid min-w-0 items-start gap-5 lg:grid-cols-[minmax(250px,0.8fr)_minmax(0,2fr)]">
          <section ref={list} tabIndex={-1} aria-label="用户每日记录列表" className="min-w-0 outline-none">
            <div className="overflow-hidden rounded-2xl border border-line">
              <h3 className="border-b border-line bg-raised/60 px-4 py-3 text-sm font-medium">用户与今日填写状态</h3>
              <ul className="zen-scroll max-h-80 divide-y divide-line overflow-y-auto lg:max-h-[calc(100dvh-370px)] lg:min-h-48">
                {data?.items.map(item => <li key={item.subject_id}>
                  <button aria-label={`查看 ${userName(item)} 的填写日期`} aria-pressed={selected?.subject_id === item.subject_id} onClick={() => chooseUser(item)} className={`min-h-20 w-full px-4 py-3 text-left transition focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent ${selected?.subject_id === item.subject_id ? 'bg-accent-wash' : 'hover:bg-raised/60'}`}>
                    <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                      <span className="min-w-0 break-words text-sm font-medium">{userName(item)}</span>
                      <span className={`shrink-0 text-xs font-medium ${item.today_completed ? 'text-[#8dc3b1] [.theme-warm_&]:text-[#456f60]' : 'text-alert-ink'}`}>{item.today_completed ? '已填写' : '今日未填写'}</span>
                    </span>
                    <span className="mt-1.5 block text-xs leading-5 text-ink-muted">{item.date_count ? `${item.date_count} 个日期 · ${item.record_count} 份记录` : '还没有已提交的记录'}{item.last_record_date && <span className="block tabular-nums">最近填写：{item.last_record_date}</span>}</span>
                  </button>
                </li>)}
              </ul>
              {loading && !data && <p className="p-8 text-center text-sm text-ink-muted">正在加载…</p>}
              {!loading && data?.items.length === 0 && <p className="p-8 text-center text-sm leading-6 text-ink-muted">{query ? '没有找到符合条件的普通用户。' : '暂无普通用户。'}</p>}
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs text-ink-muted">每页 20 人 · 第 {Math.floor(offset / 20) + 1} 页</span>
              <div className="flex gap-2"><button className={buttonStyle} disabled={!offset || loading} onClick={() => page(Math.max(0, offset - 20))}>上一页</button><button className={buttonStyle} disabled={!data?.has_more || loading} onClick={() => page(offset + 20)}>下一页</button></div>
            </div>
          </section>
          <section ref={dates} tabIndex={-1} aria-label="用户记录浏览" className="min-w-0 outline-none">
            {selected ? <UserRecords key={selected.subject_id} user={selected} refresh={refresh} onBack={() => scrollToPanel(list.current)} /> : <div className="flex min-h-72 flex-col items-center justify-center rounded-2xl border border-dashed border-line px-6 text-center"><NotebookMark className="mb-3 h-7 w-7 text-accent-ink" /><h3 className="font-medium">先选择一位用户</h3><p className="mt-2 max-w-xs text-sm leading-6 text-ink-muted">查看这位用户填过的所有日期，再选择一天查看完整记录和活动明细。</p></div>}
          </section>
        </div>
        <section className="mt-6 rounded-2xl border border-line">
          <button type="button" aria-expanded={exportOpen} onClick={() => setExportOpen(value => !value)} className="flex min-h-12 w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm text-ink-muted"><span>筛选与导出</span><span aria-hidden="true">{exportOpen ? '−' : '+'}</span></button>
          {exportOpen && <ExportPanel />}
        </section>
        <p className="mt-4 text-xs leading-6 text-ink-muted">记录详情只读。未填写不等于 0 分；旧版 0–10 与新版 0–5 不混算。页面每分钟更新今日状态，返回页面时也会刷新。</p>
        </>}
      </div>
    </div>
  </dialog>;
}

function UserRecords({ user, refresh, onBack }: { user: AdminRecordUser; refresh: number; onBack: () => void }) {
  const datesPanel = useRef<HTMLElement>(null), dayPanel = useRef<HTMLElement>(null);
  const [data, setData] = useState<AdminRecordDates | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  useEffect(() => {
    const control = new AbortController(); setLoading(true); setError('');
    void fetchAdminRecordDates(user.subject_id, control.signal)
      .then(result => { if (!control.signal.aborted) setData(result); })
      .catch(err => { if (!control.signal.aborted) setError(errorMessage(err)); })
      .finally(() => { if (!control.signal.aborted) setLoading(false); });
    return () => control.abort();
  }, [user.subject_id, refresh, retry]);
  return <>
    <button className="mb-2 min-h-11 rounded-xl px-3 text-sm text-accent-ink hover:bg-raised lg:hidden" onClick={onBack}>返回用户列表</button>
    <div className="mb-4"><h3 className="break-words text-base font-semibold">{userName(user)}</h3><p className="mt-1 break-all text-xs leading-5 text-ink-muted">账号：{user.username ?? '—'} · 用户编号：{user.subject_id}</p></div>
    <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(180px,0.65fr)_minmax(0,1.5fr)]">
      <section ref={datesPanel} tabIndex={-1} aria-label="填写日期列表" className="min-w-0 rounded-2xl border border-line outline-none">
        <h4 className="border-b border-line bg-raised/60 px-4 py-3 text-sm font-medium">填写日期{data && <span className="ml-2 text-xs font-normal text-ink-muted">共 {data.total_dates} 天</span>}</h4>
        {error && <p role="alert" className="p-4 text-sm text-alert-ink">{error}<button className="mt-1 block min-h-11 underline" onClick={() => setRetry(value => value + 1)}>重试读取日期</button></p>}
        {loading && !data && <p className="p-6 text-sm text-ink-muted">正在读取日期…</p>}
        {data?.items.length === 0 && <p className="p-6 text-sm leading-6 text-ink-muted">该用户还没有提交过每日记录。</p>}
        <ul className="zen-scroll max-h-80 divide-y divide-line overflow-y-auto xl:max-h-[calc(100dvh-350px)]">
          {data?.items.map(item => <li key={item.local_date}><button aria-label={`查看 ${item.local_date} 的全部记录`} aria-pressed={selectedDate === item.local_date} onClick={() => { setSelectedDate(item.local_date); requestAnimationFrame(() => scrollToPanel(dayPanel.current)); }} className={`min-h-16 w-full px-4 py-3 text-left transition focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent ${selectedDate === item.local_date ? 'bg-accent-wash' : 'hover:bg-raised/60'}`}><span className="block text-sm font-medium tabular-nums">{item.local_date}</span><span className="mt-1 block text-xs text-ink-muted">{item.record_count} 份记录 · {item.activity_count} 项活动</span></button></li>)}
        </ul>
      </section>
      <section ref={dayPanel} tabIndex={-1} aria-label="当日全部记录" className="min-w-0 outline-none">
        {selectedDate ? <><button className="mb-2 min-h-11 rounded-xl px-3 text-sm text-accent-ink hover:bg-raised xl:hidden" onClick={() => scrollToPanel(datesPanel.current)}>返回日期列表</button><DayRecords key={selectedDate} subjectId={user.subject_id} localDate={selectedDate} refresh={refresh} /></> : <div className="flex min-h-48 flex-col items-center justify-center rounded-2xl bg-raised/40 px-5 text-center"><p className="text-sm font-medium">选择一个填写日期</p><p className="mt-2 text-xs leading-6 text-ink-muted">展示该日全部已提交记录，包含每份记录的全部活动明细。</p></div>}
      </section>
    </div>
  </>;
}

function DayRecords({ subjectId, localDate, refresh }: { subjectId: string; localDate: string; refresh: number }) {
  const [data, setData] = useState<AdminRecordDay | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  useEffect(() => {
    const control = new AbortController(); setLoading(true); setError('');
    void fetchAdminRecordDay(subjectId, localDate, control.signal)
      .then(result => { if (!control.signal.aborted) setData(result); })
      .catch(err => { if (!control.signal.aborted) setError(errorMessage(err)); })
      .finally(() => { if (!control.signal.aborted) setLoading(false); });
    return () => control.abort();
  }, [subjectId, localDate, refresh, retry]);
  return <div className="space-y-4">
    <div><h4 className="font-medium tabular-nums">{localDate}</h4><p className="mt-1 text-xs text-ink-muted">{data ? `共 ${data.total} 份记录 · ${data.items.reduce((count, item) => count + item.record.activities.length, 0)} 项活动` : '正在读取当天记录…'}</p></div>
    {error && <p role="alert" className="rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}<button className="ml-2 min-h-11 underline" onClick={() => setRetry(value => value + 1)}>重试读取当天记录</button></p>}
    {loading && !data && <p className="p-6 text-sm text-ink-muted">正在加载记录详情…</p>}
    {data?.items.length === 0 && <p className="rounded-xl bg-raised p-4 text-sm text-ink-muted">该日当前没有已提交记录，记录日期可能已被修改。</p>}
    {data?.items.map((item, index) => <RecordDetail key={item.record.id} item={item} index={index} />)}
  </div>;
}

function RecordDetail({ item, index }: { item: AdminRecord; index: number }) {
  return <article data-record-id={item.record.id} aria-label={`第 ${index + 1} 份记录`} className="min-w-0">
    <p className="mb-2 text-xs text-ink-muted">第 {index + 1} 份记录</p>
    {/* One history component per record opens every record on this date, instead
        of silently collapsing all but the first item in a shared accordion. */}
    <AssessmentHistory key={item.record.id} records={[item.record]} loading={false} loadingMore={false} error={null} hasMore={false} onLoadMore={() => {}} onRetry={() => {}} />
  </article>;
}

function ExportPanel() {
  const [draft, setDraft] = useState<RecordFilters>(empty), [filters, setFilters] = useState<RecordFilters>(empty);
  const [data, setData] = useState<AdminRecordPage | null>(null), [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState(''), [exporting, setExporting] = useState(false);
  const exportControl = useRef<AbortController | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(filters);
  useEffect(() => () => exportControl.current?.abort(), []);
  useEffect(() => {
    const control = new AbortController(); setLoading(true); setError(''); setNotice('');
    void fetchAdminRecords(filters, 0, control.signal)
      .then(result => { if (!control.signal.aborted) setData(result); })
      .catch(err => { if (!control.signal.aborted) { setError(errorMessage(err)); setData(null); } })
      .finally(() => { if (!control.signal.aborted) setLoading(false); });
    return () => control.abort();
  }, [filters, refresh]);
  function apply() {
    if (draft.start && draft.end && draft.start > draft.end) { setError('开始日期不能晚于结束日期'); return; }
    setFilters({ ...draft });
  }
  async function download(kind: ExportKind) {
    if (exporting || !data || loading || dirty) return;
    const control = new AbortController(); exportControl.current = control; setExporting(true); setError(''); setNotice('');
    try { await exportAdminRecords(filters, kind, control.signal); if (!control.signal.aborted) setNotice('已生成下载文件，请妥善保存用户资料。'); }
    catch (err) { if (!control.signal.aborted) setError(errorMessage(err)); }
    finally { if (!control.signal.aborted) setExporting(false); }
  }
  return <div className="border-t border-line p-4">
    <p className="mb-4 text-xs leading-6 text-ink-muted">此处筛选只用于统计与 CSV 导出，不限制上方的用户列表、填写日期或当日全部记录。</p>
    <form onSubmit={event => { event.preventDefault(); apply(); }} className="grid grid-cols-2 items-end gap-3 lg:grid-cols-[minmax(180px,1.5fr)_1fr_1fr_1fr_auto]">
      <label className="col-span-2 min-w-0 lg:col-span-1"><span className="mb-1.5 block text-xs text-ink-muted">导出用户</span><input aria-label="导出用户筛选" placeholder="账号、昵称或用户编号" maxLength={64} value={draft.query} onChange={event => setDraft({ ...draft, query: event.target.value })} className={inputStyle} /></label>
      <label className="min-w-0"><span className="mb-1.5 block text-xs text-ink-muted">开始日期</span><input type="date" aria-label="开始日期" value={draft.start} onChange={event => setDraft({ ...draft, start: event.target.value })} className={inputStyle} /></label>
      <label className="min-w-0"><span className="mb-1.5 block text-xs text-ink-muted">结束日期</span><input type="date" aria-label="结束日期" value={draft.end} onChange={event => setDraft({ ...draft, end: event.target.value })} className={inputStyle} /></label>
      <label className="min-w-0"><span className="mb-1.5 block text-xs text-ink-muted">量表版本</span><WorkbenchSelect aria-label="量表版本" className="w-full" value={draft.scale_version} onChange={event => setDraft({ ...draft, scale_version: event.target.value })}><option value="">全部版本</option><option value="2">新版 · 0–5</option><option value="1">旧版 · 0–10</option></WorkbenchSelect></label>
      <div className="flex gap-2"><button type="submit" disabled={exporting} className="min-h-11 flex-1 rounded-xl bg-accent px-4 text-sm font-medium text-on-accent disabled:opacity-40">查询</button><button type="button" disabled={exporting} className={buttonStyle} onClick={() => { setDraft(empty); setFilters({ ...empty }); }}>重置</button></div>
    </form>
    <div className="flex flex-wrap items-center justify-between gap-3 py-4">
      <p role="status" className="text-sm text-ink-muted">{loading ? '正在读取导出统计…' : data ? <>{data.total} 份记录 · {data.users} 位用户 <span className="ml-2 text-xs">新版 {data.versions['2'] ?? 0} · 旧版 {data.versions['1'] ?? 0}</span></> : '暂未载入'}<span className="mt-1 block text-xs">{dirty ? '筛选条件已修改，请点击查询后导出。' : '日期按用户填写的记录日期计算。'}</span></p>
      <div className="flex flex-wrap gap-2"><button disabled={exporting || loading} className={buttonStyle} onClick={() => setRefresh(value => value + 1)}>刷新导出统计</button><button disabled={exporting || loading || dirty || !data?.total} className={buttonStyle} onClick={() => void download('daily')}>{exporting ? '正在导出…' : '导出每日汇总'}</button><button disabled={exporting || loading || dirty || !data?.total} className={buttonStyle} onClick={() => void download('activities')}>导出活动明细</button><button disabled={exporting || loading || dirty} title="按上方用户与日期筛选；每日测量没有量表版本" className={buttonStyle} onClick={() => void download('measurements')}>导出每日测量</button></div>
    </div>
    {error && <p role="alert" className="mb-3 rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}</p>}
    {notice && <p role="status" className="mb-3 rounded-xl bg-accent-wash p-3 text-sm text-accent-ink">{notice}</p>}
    <p className="text-xs leading-6 text-ink-muted">CSV 保留空值与“不适用”标记，每次最多导出 1000 份记录；活动明细一项活动一行，请勿重复累计其中的每日评分。文件含用户资料，请仅用于授权的数据收集。</p>
  </div>;
}
