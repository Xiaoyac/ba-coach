"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { API_BASE } from "@/lib/api";
import { apiHeaders, checkAuthentication } from "@/lib/http";
import ArchiveSelect from "@/components/ArchiveSelect";
import { fetchGoalHistory, goalKindLabel, scheduleKindLabel, eventKindLabel,
  type ProgramGoal, type GoalHistory, type ArchivedPlan, type ArchivedCycle, type ActivityRecord } from "@/lib/program";

type Overview = { enabled: boolean; goals: ProgramGoal[]; activity_records?: ActivityRecord[] };
type Props = { open: boolean; sessionId: string | null; busy?: boolean; refreshKey: number; onClose: () => void; onOpenReminders?: () => void };
type HistoryTab = "plans" | "cycles" | "activities";
const states: Record<string, string> = {draft:"待完善",active:"进行中",paused:"已暂停",completed:"已完成",abandoned:"已结束",replaced:"已替换"};
const cycleStates: Record<string, string> = {planning:"计划中",waiting_execution:"等待执行",reviewing:"复盘中",completed:"本轮已完成",cancelled:"已取消"};
const planStates: Record<string, string> = {draft:"草稿 · 尚未确认",confirmed:"已确认",superseded:"历史版本"};
const actions: Record<string, string> = {continue:"继续原计划",adjust:"调整计划",pause:"暂停目标",end:"结束目标",replace_keep:"新目标，保留原目标",replace_pause:"新目标，暂停原目标"};
const tabs = [{id:"plans",label:"PA 计划卡"},{id:"cycles",label:"执行与复盘"},{id:"activities",label:"活动记录"}] as const;
const statusFilters = [{id:"all",label:"全部"},{id:"active",label:"进行中"},{id:"draft",label:"待完善"},{id:"paused",label:"已暂停"},{id:"finished",label:"已归档"}];
const finished = new Set(["completed","abandoned","replaced"]);
const kindOf = (goal: ProgramGoal) => goal.goal_kind ?? "unclassified";
function timestamp(value?: string | null) { if (!value) return null; const n = Date.parse(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`); return Number.isNaN(n) ? null : n; }
function date(value?: string | null) { const time = timestamp(value); return time === null ? "时间未记录" : new Date(time).toLocaleDateString("zh-CN",{year:"numeric",month:"short",day:"numeric"}); }
// Order the entire owned archive before applying filters: filtering must not
// renumber a user's goals or turn an execution cycle into a new goal.
function chronological(a: ProgramGoal, b: ProgramGoal) { return (timestamp(a.created_at) ?? Infinity) - (timestamp(b.created_at) ?? Infinity) || a.id.localeCompare(b.id); }
function matches(goal: ProgramGoal, status: string) { return status === "all" || (status === "finished" ? finished.has(goal.status) : goal.status === status); }

export default function GoalOverview({open,sessionId,refreshKey,onClose,onOpenReminders}:Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [overview,setOverview] = useState<Overview|null>(null);
  const [loading,setLoading] = useState(true);
  const [error,setError] = useState("");
  const [retry,setRetry] = useState(0);
  const [selected,setSelected] = useState<ProgramGoal|null>(null);
  const [status,setStatus] = useState("all");
  const [kind,setKind] = useState("all");
  const [search,setSearch] = useState("");
  const [sort,setSort] = useState("updated");
  const [viewMode,setViewMode] = useState<"timeline"|"cards">("timeline");
  const [oldestFirst,setOldestFirst] = useState(false);
  const archiveScroll = useRef<HTMLDivElement>(null);
  const archivePosition = useRef(0);
  const lastOpened = useRef<string|null>(null);
  const [currentGoal,setCurrentGoal] = useState<string|null>(null);
  useEffect(()=> { if(open && !dialog.current?.open) dialog.current?.showModal(); if(!open) dialog.current?.close(); },[open]);
  useEffect(()=> {
    if(!open) return;
    const controller = new AbortController();
    const signal = AbortSignal.any([controller.signal,AbortSignal.timeout(15000)]);
    setLoading(true); setError(""); setOverview(null); setSelected(null); setCurrentGoal(null);
    async function load() {
      try {
        const response=await fetch(`${API_BASE}/api/program/goals/overview`,{headers:apiHeaders(),signal,cache:"no-store"});
        checkAuthentication(response);
        if(!response.ok) throw new Error("目标档案暂时无法加载，请稍后重试。");
        const data:Overview=await response.json();
        if(controller.signal.aborted) return;
        setOverview(data);
        setLoading(false);
        if(sessionId && data.enabled) {
          // The optional current-chat badge must not block the account archive.
          try {
            const result=await fetch(`${API_BASE}/api/program/${encodeURIComponent(sessionId)}`,{headers:apiHeaders(),signal,cache:"no-store"});
            checkAuthentication(result);
            if(result.ok) { const state=await result.json(); if(!controller.signal.aborted)setCurrentGoal(state.runtime?.active_goal_id ?? null); }
          } catch { /* Archive remains usable when only chat metadata is unavailable. */ }
        }
      } catch(e) { if(!controller.signal.aborted)setError(e instanceof Error && e.name === "TimeoutError" ? "加载超时，请重试。" : "目标档案暂时无法加载，请稍后重试。"); }
      finally { if(!controller.signal.aborted)setLoading(false); }
    }
    void load(); return ()=>controller.abort();
  },[open,sessionId,refreshKey,retry]);
  const visible=useMemo(()=> {
    const query=search.trim().toLowerCase();
    return (overview?.goals??[]).filter(g=>matches(g,status) && (kind==="all" || kindOf(g)===kind)
      && `${g.title} ${g.long_term_direction??""} ${g.plan?.activity_content??""}`.toLowerCase().includes(query))
      .sort((a,b)=>{
        if(viewMode === "timeline") {
          // Undated imports are always last; never invent their chronology.
          if(timestamp(a.created_at)===null || timestamp(b.created_at)===null) return chronological(a,b);
          return oldestFirst ? chronological(a,b) : chronological(b,a);
        }
        return sort==="title"?a.title.localeCompare(b.title,"zh-CN"):(b[sort==="created"?"created_at":"updated_at"]??"").localeCompare(a[sort==="created"?"created_at":"updated_at"]??"");
      });
  },[overview,status,kind,search,sort,viewMode,oldestFirst]);
  const goalNumbers = useMemo(()=>new Map((overview?.goals??[]).filter(g=>timestamp(g.created_at)!==null).sort(chronological).map((g,i)=>[g.id,i+1])),[overview]);
  useEffect(()=> { archiveScroll.current?.scrollTo({top:0}); },[status,kind,search,sort,viewMode,oldestFirst]);
  useEffect(()=> {
    if(selected || !lastOpened.current) return;
    archiveScroll.current?.scrollTo({top:archivePosition.current});
    const button=Array.from(archiveScroll.current?.querySelectorAll<HTMLButtonElement>('button[data-goal-id]')??[]).find(b=>b.dataset.goalId===lastOpened.current);
    button?.focus({preventScroll:true});
  },[selected]);
  const openGoal=(goal:ProgramGoal)=>{archivePosition.current=archiveScroll.current?.scrollTop??0;lastOpened.current=goal.id;setSelected(goal);};
  const reset=()=>{setStatus("all");setKind("all");setSearch("");};
  const goals=overview?.goals??[];
  return <dialog ref={dialog} onClose={onClose} aria-labelledby="goal-archive-title" className="goal-archive m-auto h-[min(860px,calc(100dvh-40px))] w-[min(1180px,calc(100vw-48px))] max-w-none overflow-hidden rounded-3xl border-0 bg-sheet p-0 text-ink shadow-2xl backdrop:bg-black/40 max-sm:h-[100dvh] max-sm:max-h-[100dvh] max-sm:w-screen max-sm:rounded-none">
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-start justify-between gap-4 px-6 pb-3 pt-5 sm:px-9 sm:pb-5 sm:pt-6">
        <div><p className={`text-xs tracking-[.16em] text-accent-ink ${selected ? "hidden sm:block" : ""}`}>PA · 行动档案</p><h2 id="goal-archive-title" className={`mt-1.5 font-semibold tracking-tight sm:text-3xl ${selected ? "text-xl" : "text-2xl"}`}>我的目标</h2><p className={`mt-2 text-sm text-ink-muted ${selected ? "hidden sm:block" : ""}`}>每一个小小的尝试，都有自己的轨迹。</p></div>
        <button onClick={onClose} aria-label="关闭目标总览" className="grid size-11 shrink-0 place-items-center rounded-full text-2xl text-ink-muted hover:bg-raised">×</button>
      </header>
      {selected ? <GoalDetails goal={selected} onBack={()=>setSelected(null)} /> : <>
        <div className="shrink-0 px-6 sm:px-9">
          <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-ink-muted"><span><b className="mr-1.5 text-base font-medium text-ink">{goals.length}</b>个目标</span><span><b className="mr-1.5 text-base font-medium text-ink">{goals.filter(g=>g.status==="active").length}</b>个正在进行</span><span className="hidden sm:ml-auto sm:inline">在对话中制定，在这里回顾 · 不会自动切换当前目标</span></div>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex rounded-xl bg-raised p-1" role="group" aria-label="目标展示方式">
              <button aria-pressed={viewMode==="timeline"} onClick={()=>setViewMode("timeline")} className={`min-h-10 rounded-lg px-4 text-sm ${viewMode==="timeline"?"bg-panel font-medium text-accent-ink shadow-sm":"text-ink-muted"}`}>时间线</button>
              <button aria-pressed={viewMode==="cards"} onClick={()=>setViewMode("cards")} className={`min-h-10 rounded-lg px-4 text-sm ${viewMode==="cards"?"bg-panel font-medium text-accent-ink shadow-sm":"text-ink-muted"}`}>卡片总览</button>
            </div>
            <span className="hidden text-xs leading-5 text-ink-muted sm:inline">{viewMode==="timeline"?"沿着时间，看看每次为自己定下的行动。":"保留熟悉的卡片，随时切回时间线。"}</span>
          </div>
          <div className="flex gap-2 pb-2">
            <input aria-label="搜索目标" placeholder="搜索目标或活动…" value={search} onChange={e=>setSearch(e.target.value)} className="archive-input w-0 min-w-0 flex-1" />
            {viewMode==="timeline"?<ArchiveSelect label="时间线顺序" value={oldestFirst?"oldest":"newest"} onChange={value=>setOldestFirst(value==="oldest")} options={[{value:"newest",label:"最近设定在前"},{value:"oldest",label:"从第一次开始"}]} />:<ArchiveSelect label="排序方式" value={sort} onChange={setSort} options={[{value:"updated",label:"最近更新"},{value:"created",label:"最近创建"},{value:"title",label:"按标题"}]} />}
          </div>
          <details className="pb-2 text-xs text-ink-muted">
            <summary className="w-fit cursor-pointer py-2">筛选目标{status!=="all"||kind!=="all"?" · 已筛选":" · 状态 / 类型"}</summary>
            <div className="grid grid-cols-2 gap-2 py-2 sm:flex">
              <ArchiveSelect label="目标状态" value={status} onChange={setStatus} options={statusFilters.map(f=>({value:f.id,label:`${f.label}（${goals.filter(g=>matches(g,f.id)).length}）`}))} />
              <ArchiveSelect label="目标类型" value={kind} onChange={setKind} options={[{value:"all",label:"所有类型"},...Object.entries(goalKindLabel).map(([value,label])=>({value,label}))]} />
              {(status!=="all"||kind!=="all")&&<button className="min-h-10 text-accent-ink" onClick={reset}>重置条件</button>}
            </div>
          </details>
        </div>
        <div ref={archiveScroll} className="zen-scroll min-h-0 flex-1 overflow-y-auto bg-raised/25 px-6 py-5 sm:px-9">
          {onOpenReminders&&<div className="mb-4 flex items-center justify-between gap-3 text-xs text-ink-muted"><span>给每次尝试留一点回顾的空间</span><button onClick={onOpenReminders} className="surface-button min-h-11 shrink-0 rounded-xl px-3 text-accent-ink">活动后提醒</button></div>}
          {loading?<Empty title="正在整理目标档案…"/>:error?<Empty title="加载未完成" detail={error}><button className="archive-action" onClick={()=>setRetry(n=>n+1)}>重新加载</button></Empty>:!overview?.enabled?<Empty title="目标档案尚未启用" detail="当前环境暂不支持目标历史。"/>:!visible.length?<Empty title={goals.length?"没有找到匹配的目标":"还没有目标，先从聊聊开始"} detail={goals.length?"试试其他关键词，或查看全部状态。":"不用在这里填表。在聊天中和教练决定想尝试的事，目标就会保存到这里。"}>{goals.length>0&&<button className="archive-action" onClick={reset}>清除筛选</button>}</Empty>:viewMode==="timeline"?<>
            <p className="mb-5 text-xs leading-5 text-ink-muted">按设定顺序串起每个目标。点开卡片，回顾计划与每轮执行。</p>
            <ol aria-label="目标设定时间线" className="goal-timeline mx-auto max-w-4xl">
              {visible.map(goal=><li key={goal.id} className="goal-timeline-entry" data-goal-number={goalNumbers.get(goal.id)??"unknown"}>
                <div className="goal-timeline-date"><p className="text-sm font-semibold text-accent-ink">{goalNumbers.has(goal.id)?`第 ${goalNumbers.get(goal.id)} 个目标`:"早期档案 · 顺序未知"}</p><p className="mt-1 text-xs leading-5 text-ink-muted">{date(goal.created_at)}</p></div>
                <span aria-hidden="true" className={`goal-timeline-dot ${goal.status==="active"?"bg-accent":"bg-panel"}`} />
                <GoalCard goal={goal} timeline current={currentGoal===goal.id} onOpen={()=>openGoal(goal)}/>
              </li>)}
            </ol>
          </>:<div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{visible.map(goal=><GoalCard key={goal.id} goal={goal} current={currentGoal===goal.id} onOpen={()=>openGoal(goal)} />)}</div>}
          {!loading&&!error&&!!overview?.activity_records?.length&&<details className="mt-7 text-sm"><summary className="cursor-pointer py-3 text-ink-muted">未归属目标的近期活动 · {overview.activity_records.length}</summary><p className="mb-3 text-xs text-ink-faint">临时活动与想法不会自动成为目标，也不算完成其他目标。</p><div className="space-y-2">{overview.activity_records.map(record=><div key={record.id} className="flex items-start justify-between gap-4 rounded-xl bg-panel p-3"><span>{record.activity_content}</span><span className="shrink-0 text-xs text-ink-muted">{eventKindLabel[record.event_kind]}</span></div>)}</div></details>}
        </div>
        <footer className="shrink-0 px-6 py-3 text-xs leading-5 text-ink-faint sm:px-9">这里保留现有的计划版本与执行历史。暂停不等于失败；想继续时，在新对话中选择这个目标。</footer>
      </>}
    </div>
  </dialog>;
}

function GoalCard({goal,current,onOpen,timeline=false}:{goal:ProgramGoal;current:boolean;onOpen:()=>void;timeline?:boolean}) {
  const plan=goal.plan;
  return <button data-goal-id={goal.id} onClick={onOpen} className={`goal-archive-card flex min-w-0 flex-col rounded-2xl bg-panel p-5 text-left shadow-sm transition-shadow hover:shadow-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${timeline?"w-full sm:p-6":"min-h-64"}`}>
    <div className="flex items-center justify-between gap-2 text-xs"><span className="text-ink-muted">{goalKindLabel[kindOf(goal)]}</span><span className={`rounded-full px-2.5 py-1 ${goal.status==="active"?"bg-accent-wash text-accent-ink":"bg-raised text-ink-muted"}`}>{states[goal.status]??"其他状态"}</span></div>
    <h3 className="mt-4 break-words text-lg font-semibold leading-7">{goal.title}</h3>
    <p className="mt-2 line-clamp-2 text-sm leading-6 text-ink-muted">{plan?.activity_content??(goal.status==="draft"?"计划仍在讨论中，打开查看草稿。":"打开查看这项行动的完整记录。")}</p>
    {goal.long_term_direction&&<p className="mt-2 line-clamp-2 text-xs leading-5 text-ink-faint">方向 · {goal.long_term_direction}</p>}
    <div className={`mt-4 text-xs leading-5 text-ink-muted ${timeline?"grid gap-3 rounded-xl bg-raised/55 p-3 sm:grid-cols-2":"space-y-1"}`}><p>执行安排 · {plan?.schedule_text??"尚未确认"}{plan?.duration_minutes!=null?` · ${plan.duration_minutes} 分钟`:""}</p><p>复盘周期 · {goal.latest_cycle?`第 ${goal.latest_cycle.ordinal} 轮 · ${cycleStates[goal.latest_cycle.status]??"待更新"}`:"尚未开始"}</p></div>
    <div className="mt-auto flex items-center justify-between gap-2 pt-5 text-xs"><span className="text-ink-faint">{current?"当前对话目标":timestamp(goal.updated_at)===null?"历史档案":`${date(goal.updated_at)} 更新`}</span><span className="font-medium text-accent-ink">查看完整卡片 ↗</span></div>
  </button>;
}

function GoalDetails({goal,onBack}:{goal:ProgramGoal;onBack:()=>void}) {
  const [tab,setTab]=useState<HistoryTab>("plans"), [page,setPage]=useState(1);
  const [data,setData]=useState<GoalHistory|null>(null), [loading,setLoading]=useState(true);
  const [error,setError]=useState(""), [retry,setRetry]=useState(0);
  const scroll=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError("");
    fetchGoalHistory(goal.id,page,AbortSignal.any([controller.signal,AbortSignal.timeout(15000)]))
      .then(value=>{if(!controller.signal.aborted)setData(value);})
      .catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:"加载失败");})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    scroll.current?.scrollTo({top:0});return()=>controller.abort();
  },[goal.id,page,retry]);
  const total=data?.totals[tab]??0, pages=Math.max(1,Math.ceil(total/(data?.page_size??12)));
  return <div className="flex min-h-0 flex-1 flex-col">
    <div className="shrink-0 px-6 sm:px-9"><button onClick={onBack} className="mb-3 min-h-10 text-sm text-accent-ink">← 返回目标列表</button><div className="flex flex-wrap items-baseline gap-3"><h3 className="break-words text-xl font-semibold">{goal.title}</h3><span className="text-xs text-ink-muted">{goalKindLabel[kindOf(goal)]} · {states[goal.status]}</span></div>{goal.long_term_direction&&<p className="mt-2 text-sm text-ink-muted">长期方向 · {goal.long_term_direction}</p>}<p className="mt-2 text-xs text-ink-faint">创建于 {date(goal.created_at)} · 历史资料只读，调整请在对话中与教练讨论。</p>
      <div className="mt-4 flex gap-1 overflow-x-auto pb-3" role="group" aria-label="目标历史分类">{tabs.map(t=><button key={t.id} aria-pressed={tab===t.id} onClick={()=>{setTab(t.id);setPage(1);scroll.current?.scrollTo({top:0});}} className={`min-h-11 shrink-0 rounded-full px-3 text-xs sm:px-4 sm:text-sm ${tab===t.id?"bg-accent-wash text-accent-ink":"text-ink-muted hover:bg-raised"}`}>{t.label} <span className="ml-1 opacity-60">{data?.totals[t.id]??"—"}</span></button>)}</div>
    </div>
    <div ref={scroll} className="zen-scroll min-h-0 flex-1 overflow-y-auto bg-raised/25 px-6 py-5 sm:px-9">
      {loading?<Empty title="正在加载历史…"/>:error?<Empty title="暂时无法打开" detail={error}><button className="archive-action" onClick={()=>setRetry(x=>x+1)}>重试</button></Empty>:!total?<Empty title="这里还没有记录" detail={tab==="plans"?"教练与你讨论计划后，PA 卡片会出现在这里。":tab==="cycles"?"执行周期建立后，可以在这里查看每轮安排和复盘。":"在对话中反馈的、明确属于这个目标的活动会显示在这里。"}/>:<div className="space-y-4">
        {tab==="plans"&&data?.plans.map(plan=><PlanCard key={plan.id} plan={plan}/>)}
        {tab==="cycles"&&data?.cycles.map(cycle=><CycleCard key={cycle.id} cycle={cycle}/>)}
        {tab==="activities"&&data?.activities.map(event=><article key={event.id} className="rounded-2xl bg-panel p-5"><div className="flex flex-wrap justify-between gap-2"><h4 className="font-medium">{event.activity_content}</h4><span className="text-xs text-ink-muted">{eventKindLabel[event.event_kind]}{event.status==="superseded"?" · 已被更正":""}</span></div><p className="mt-2 text-xs text-ink-faint">{event.occurred_at_text??date(event.created_at)}{event.cycle_ordinal?` · 第 ${event.cycle_ordinal} 轮`:""}</p>{event.effect&&<p className="mt-3 text-sm leading-6 text-ink-muted">{event.effect}</p>}{event.status==="superseded"&&<p className="mt-2 text-xs text-ink-faint">仅供追溯，不作为当前有效记录。</p>}</article>)}
      </div>}
    </div>
    <footer className="flex shrink-0 items-center justify-between gap-3 px-6 py-3 text-xs text-ink-muted sm:px-9"><span>共 {total} 条 · 第 {Math.min(page,pages)} / {pages} 页</span><div className="flex gap-2"><button className="archive-action" disabled={loading||page<=1} onClick={()=>setPage(p=>p-1)}>上一页</button><button className="archive-action" disabled={loading||page>=pages} onClick={()=>setPage(p=>p+1)}>下一页</button></div></footer>
  </div>;
}

function PlanCard({plan}:{plan:ArchivedPlan}) {
  return <article className="rounded-2xl bg-panel p-5 sm:p-6"><div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-semibold">PA 目标卡 · 第 {plan.version_no} 版</h4><span className="text-xs text-accent-ink">{planStates[plan.record_status]??plan.record_status}</span></div><p className="mt-1 text-xs text-ink-faint">{date(plan.created_at)} 建立 · {date(plan.updated_at)} 更新</p><p className="mt-5 whitespace-pre-wrap text-lg font-medium leading-7">{plan.activity_content??"执行内容仍在讨论"}</p><dl className="mt-5 grid gap-x-8 gap-y-4 sm:grid-cols-2"><Field label="执行时间 / 频率" value={plan.schedule_text}/><Field label="单次时长" value={plan.duration_minutes!=null?`${plan.duration_minutes} 分钟`:null}/><Field label="安排类型" value={scheduleKindLabel[plan.schedule_kind??"unspecified"]}/><Field label="复盘节奏" value={plan.review_cadence}/><Field label="执行地点" value={plan.location}/><Field label="同行 / 支持" value={plan.companion}/></dl><details className="mt-5"><summary className="cursor-pointer py-2 text-sm text-accent-ink">支持条件与应对计划</summary><dl className="mt-3 grid gap-4 sm:grid-cols-2"><Field label="在意的价值" value={plan.core_values}/><Field label="与价值的关系" value={plan.core_values_impact}/><Field label="可能遇到的困难" value={plan.potential_barriers}/><Field label="应对办法" value={plan.barrier_coping_plan}/><Field label="难度感受" value={plan.difficulty}/><Field label="可用支持" value={plan.resources}/></dl></details>{plan.record_status==="draft"&&<p className="mt-4 text-xs leading-5 text-ink-muted">这是一份待完善的草稿，不代表你已经承诺执行。</p>}</article>;
}
function CycleCard({cycle}:{cycle:ArchivedCycle}) {
  return <article className="rounded-2xl bg-panel p-5 sm:p-6"><div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-semibold">第 {cycle.ordinal} 轮</h4><span className="text-xs text-accent-ink">{cycleStates[cycle.status]??cycle.status}</span></div><p className="mt-2 text-sm text-ink-muted">{cycle.plan_version?`执行第 ${cycle.plan_version} 版计划` : "计划尚未确认"}{cycle.schedule_text?` · ${cycle.schedule_text}`:""}</p>{cycle.activity_content&&<p className="mt-3 text-sm leading-6">{cycle.activity_content}</p>}<dl className="mt-4 grid gap-4 sm:grid-cols-3"><Field label="建立时间" value={date(cycle.created_at)}/><Field label="开始执行" value={date(cycle.started_at)}/><Field label="本轮结束" value={date(cycle.completed_at)}/></dl>{(cycle.review_summary||cycle.review_action)&&<div className="mt-5 rounded-xl bg-raised/65 p-4"><p className="text-xs font-medium text-accent-ink">本轮复盘{cycle.review_status!=="confirmed"?" · 尚未确认":" · 已确认"}</p>{cycle.review_summary&&<p className="mt-2 whitespace-pre-wrap text-sm leading-6">{cycle.review_summary}</p>}{cycle.review_action&&<p className="mt-2 text-xs text-ink-muted">下一步 · {actions[cycle.review_action]??"继续讨论"}</p>}</div>}</article>;
}
const nestedNames:Record<string,string>={text:"内容",barrier:"困难",plan:"应对",frequency:"频率",days_per_week:"每周天数",times_per_week:"每周次数",unit:"单位",interval:"间隔",days:"日期",type:"类型"};
function readable(value:unknown):string {if(value==null||value==="")return "尚未记录";if(Array.isArray(value))return value.length?value.map(readable).join("；"):"尚未记录";if(typeof value==="object")return Object.entries(value).filter(([k])=>k!=="schema_version"&&!k.startsWith("_")).map(([k,v])=>`${nestedNames[k]??k}：${readable(v)}`).join("；");return String(value);}
function Field({label,value}:{label:string;value:unknown}){return <div className="min-w-0"><dt className="text-xs text-ink-faint">{label}</dt><dd className="mt-1 break-words whitespace-pre-wrap text-sm leading-6">{readable(value)}</dd></div>;}
function Empty({title,detail,children}:{title:string;detail?:string;children?:ReactNode}){return <div className="mx-auto max-w-md px-4 py-12 text-center"><p className="text-lg font-medium">{title}</p>{detail&&<p className="mt-3 text-sm leading-6 text-ink-muted">{detail}</p>}<div className="mt-5">{children}</div></div>;}
