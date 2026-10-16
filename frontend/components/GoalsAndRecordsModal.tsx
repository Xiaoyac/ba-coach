"use client";

import { useParticipantStyle } from "@/lib/useParticipantStyle";
import { useCallback, useEffect, useRef, useState } from "react";
import GoalOverview from "@/components/GoalOverview";
import DailyAssessmentModal from "@/components/DailyAssessmentModal";

type Tab = "goals" | "record";

/** One modal owns focus; both panels stay mounted while changing tabs so a
 * partially filled daily record and the archive position remain intact. */
export default function GoalsAndRecordsModal({ initialTab = "goals", initialRecordView = "record", initialGoalKind = "all", sessionId, refreshKey, onSaved, onClose, onOpenReminders }: {
  initialTab?: Tab;
  initialRecordView?: "record" | "history";
  initialGoalKind?: "all" | "primary";
  sessionId: string | null;
  refreshKey: number;
  onSaved: () => void;
  onClose: () => void;
  onOpenReminders: () => void;
}) {
  const participant = useParticipantStyle();
  const dialog = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [recordVisited, setRecordVisited] = useState(initialTab === "record");
  const [draft, setDraft] = useState({ dirty: false, busy: false });
  const [pendingClose, setPendingClose] = useState<(() => void) | null>(null);
  const reportDraft = useCallback((state: { dirty: boolean; busy: boolean }) => setDraft(previous => previous.dirty === state.dirty && previous.busy === state.busy ? previous : state), []);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    if (!draft.dirty) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [draft.dirty]);
  function selectTab(next: Tab) {
    if (next === "record") setRecordVisited(true);
    setTab(next);
  }
  function requestClose(action = onClose) {
    if (draft.busy) return;
    if (draft.dirty) setPendingClose(() => action);
    else action();
  }
  return <dialog ref={dialog} aria-labelledby="goals-records-title"
    onCancel={event => { event.preventDefault(); requestClose(); }}
    className="default-style:m-auto default-style:h-[min(900px,calc(100dvh-40px))] default-style:max-h-[calc(100dvh-40px)] default-style:w-[min(1180px,calc(100vw-40px))] default-style:max-w-none default-style:overflow-hidden default-style:rounded-3xl default-style:border-0 default-style:bg-sheet default-style:p-0 default-style:text-ink default-style:shadow-2xl default-style:backdrop:bg-black/40 default-style:max-sm:h-[100dvh] default-style:max-sm:max-h-[100dvh] default-style:max-sm:w-screen default-style:max-sm:rounded-none simple:m-auto simple:h-[min(900px,calc(100dvh-40px))] simple:max-h-[calc(100dvh-40px)] simple:w-[min(1180px,calc(100vw-40px))] simple:max-w-none simple:overflow-hidden simple:rounded-2xl simple:border-0 simple:bg-sheet simple:p-0 simple:text-ink simple:depth-panel simple:backdrop:bg-black/40 simple:max-sm:h-[100dvh] simple:max-sm:max-h-[100dvh] simple:max-sm:w-screen simple:max-sm:rounded-none">
    <div className="flex h-full min-h-0 flex-col">
      <header className="default-style:shrink-0 default-style:border-b default-style:border-line default-style:px-5 default-style:pb-3 default-style:pt-4 default-style:sm:px-7 simple:shrink-0 simple:px-5 simple:pb-3 simple:pt-4 simple:sm:px-7">
        <div className="flex items-center justify-between gap-3">
          <h2 id="goals-records-title" className="text-lg font-semibold">{participant ? "活动计划与记录" : "目标与记录"}</h2>
          <button type="button" disabled={draft.busy} onClick={() => requestClose()} aria-label="关闭目标与记录" className="grid size-11 place-items-center rounded-full text-xl text-ink-muted hover:bg-raised disabled:opacity-40">×</button>
        </div>
        <div role="tablist" aria-label="目标与记录导航" className="default-style:mt-1 default-style:flex default-style:gap-2 simple:mt-1 simple:flex simple:gap-4">
          {([['goals', '目标'], ['record', '每日记录']] as const).map(([id, label]) => <button key={id} id={`workspace-${id}-tab`} type="button" role="tab" aria-selected={tab === id} aria-controls={`workspace-${id}-panel`} tabIndex={tab === id ? 0 : -1}
            onClick={() => selectTab(id)} onKeyDown={event => {
              if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
              event.preventDefault(); const next = event.key === 'Home' ? 'goals' : event.key === 'End' ? 'record' : tab === 'goals' ? 'record' : 'goals';
              selectTab(next); document.getElementById(`workspace-${next}-tab`)?.focus();
            }} className={`default-style:min-h-11 default-style:rounded-xl default-style:px-5 default-style:text-sm simple:min-h-11 simple:rounded-lg simple:px-3 simple:text-sm ${tab === id ? 'default-style:bg-accent-wash default-style:font-medium default-style:text-accent-ink simple:bg-accent-wash simple:font-medium simple:text-accent-ink' : 'default-style:text-ink-muted default-style:hover:bg-raised simple:text-ink-muted simple:hover:bg-raised simple:hover:text-ink'}`}>{label}{id === 'record' && draft.dirty && <span className="ml-2 text-xs font-normal">未保存</span>}</button>)}
        </div>
      </header>
      {pendingClose && <div role="alert" className="shrink-0 border-b border-accent-edge bg-accent-wash px-5 py-3 text-sm sm:px-7">
        <p>每日记录还有未保存的内容，离开后将丢失。</p>
        <div className="mt-2 flex flex-wrap gap-2"><button type="button" onClick={() => { setPendingClose(null); selectTab('record'); }} className="min-h-11 rounded-xl bg-accent px-4 text-on-accent">继续填写</button><button type="button" onClick={() => { const action = pendingClose; setPendingClose(null); action(); }} className="min-h-11 rounded-xl px-4 text-ink-muted hover:bg-raised">放弃填写并离开</button></div>
      </div>}
      <section id="workspace-goals-panel" role="tabpanel" aria-labelledby="workspace-goals-tab" hidden={tab !== 'goals'} className="min-h-0 flex-1">
        <div className="flex h-full min-h-0 flex-col">
          <div className="default-style:flex default-style:shrink-0 default-style:flex-wrap default-style:items-center default-style:justify-between default-style:gap-1 default-style:border-b default-style:border-line default-style:px-5 default-style:py-2 default-style:text-xs default-style:text-ink-muted default-style:sm:px-7 simple:flex simple:shrink-0 simple:flex-wrap simple:items-center simple:justify-between simple:gap-1 simple:px-5 simple:py-2 simple:text-xs simple:text-ink-muted simple:sm:px-7"><span>{participant ? "查看已保存的活动安排" : "在这里查看计划与执行；设置和确认目标仍在对话中完成。"}</span><button type="button" onClick={() => requestClose()} className="min-h-11 px-2 text-accent-ink">返回对话讨论目标</button></div>
          <div className="min-h-0 flex-1"><GoalOverview embedded open initialKind={initialGoalKind} sessionId={sessionId} refreshKey={refreshKey} onClose={() => requestClose()} onOpenReminders={() => requestClose(() => { onClose(); onOpenReminders(); })} /></div>
        </div>
      </section>
      <section id="workspace-record-panel" role="tabpanel" aria-labelledby="workspace-record-tab" hidden={tab !== 'record'} className="min-h-0 flex-1">
        {recordVisited && <DailyAssessmentModal embedded initialView={initialRecordView} sessionId={sessionId} onSaved={onSaved} onClose={() => requestClose()} onDraftStateChange={reportDraft} />}
      </section>
    </div>
  </dialog>;
}
