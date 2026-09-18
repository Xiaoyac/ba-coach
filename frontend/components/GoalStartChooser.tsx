"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE } from "@/lib/api";
import { apiHeaders, checkAuthentication } from "@/lib/http";
import { goalKindLabel, type GoalKind, type ProgramGoal } from "@/lib/program";

type Props = { open: boolean; sessionId: string | null; busy?: boolean; refreshKey: number; onClose: () => void; onSelectExisting: (goalId: string, resume: boolean) => Promise<void>; onDiscussNew: () => void };
type Overview = { enabled: boolean; m1_reusable: boolean; goals: ProgramGoal[] };

const status: Record<string, string> = { draft: "草稿", active: "进行中", paused: "已暂停 · 选择即恢复" };
const groups: GoalKind[] = ["primary", "secondary", "unclassified"];
const kindOf = (goal: ProgramGoal): GoalKind => goal.goal_kind ?? "unclassified";

export default function GoalStartChooser({ open, sessionId, busy = false, refreshKey, onClose, onSelectExisting, onDiscussNew }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [goals, setGoals] = useState<ProgramGoal[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [m1, setM1] = useState(false);
  const [current, setCurrent] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => { const dialog = ref.current; if (!dialog) return; if (open && !dialog.open) dialog.showModal(); if (!open && dialog.open) dialog.close(); }, [open]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setError(""); setLoading(true); setCurrent(null);
    void (async () => {
      try {
        const overviewResponse = await fetch(`${API_BASE}/api/program/goals/overview`, { headers: apiHeaders(), signal: controller.signal, cache: "no-store" });
        checkAuthentication(overviewResponse);
        if (!overviewResponse.ok) throw new Error("目标暂时无法加载。");
        const overview = await overviewResponse.json() as Overview;
        setEnabled(overview.enabled); setM1(overview.m1_reusable);
        setGoals((overview.goals ?? []).filter((goal) => ["draft", "active", "paused"].includes(goal.status)));
        if (sessionId) {
          const sessionResponse = await fetch(`${API_BASE}/api/program/${encodeURIComponent(sessionId)}`, { headers: apiHeaders(), signal: controller.signal, cache: "no-store" });
          checkAuthentication(sessionResponse);
          if (sessionResponse.ok) setCurrent((await sessionResponse.json() as { runtime?: { active_goal_id?: string | null } }).runtime?.active_goal_id ?? null);
        }
      } catch (err) { if ((err as Error).name !== "AbortError") setError(err instanceof Error ? err.message : "加载失败。"); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    })();
    return () => controller.abort();
  }, [open, refreshKey, sessionId]);

  const select = async (id: string) => {
    if (saving || busy || !enabled || !m1 || current) return;
    setSaving(true);
    try { await onSelectExisting(id, goals.find(goal => goal.id === id)?.status === "paused"); onClose(); }
    catch (err) { setError(err instanceof Error ? err.message : "选择失败，请重试。"); }
    finally { setSaving(false); }
  };
  const disabled = Boolean(current) || busy || saving || loading || !enabled || !m1;

  return <dialog ref={ref} aria-labelledby="goal-start-title" onCancel={(event) => { if (busy || saving) event.preventDefault(); }} onClose={() => { if (!busy && !saving && open) onClose(); }}
    className="m-auto w-[min(640px,calc(100vw-24px))] max-h-[min(760px,calc(100dvh-24px))] overflow-hidden rounded-[22px] border border-line-strong bg-panel p-0 text-ink depth-panel backdrop:bg-black/55">
    <div className="max-h-[min(760px,calc(100dvh-24px))] overflow-y-auto p-5 sm:p-7"><div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold tracking-[.12em] text-accent-ink">新对话</p><h2 id="goal-start-title" className="mt-2 text-2xl font-semibold tracking-tight">这段对话从哪里开始？</h2><p className="mt-2 text-sm leading-6 text-ink-muted">继续一个已有目标，或先和 Agent 探索一个新的方向。</p></div><button type="button" onClick={onClose} disabled={busy || saving} className="grid size-10 place-items-center rounded-full border border-line text-xl disabled:opacity-45" aria-label="关闭">×</button></div>
      {error && <p role="alert" className="mt-5 rounded-xl border border-alert-edge bg-alert-wash p-3 text-sm text-alert-ink">{error}</p>}
      {current && <p className="mt-5 rounded-xl border border-alert-edge bg-alert-wash p-3 text-sm text-alert-ink">当前对话已有目标，不能在这里再次选择。</p>}
      {loading && <p role="status" className="mt-5 text-sm text-ink-muted">正在读取你的目标…</p>}
      {!loading && !enabled && <p className="mt-5 rounded-xl border border-line bg-raised p-3 text-sm text-ink-muted">V2 目标功能尚未开启。</p>}
      {enabled && !m1 && <p className="mt-5 rounded-xl border border-line bg-raised p-3 text-sm text-ink-muted">请先完成 M1，之后才能选择或讨论新目标。</p>}
      {groups.map((kind) => { const matching = goals.filter((goal) => kindOf(goal) === kind); if (!matching.length) return null; return <section key={kind} className="mt-6"><h3 className="mb-2 text-xs font-semibold tracking-[.08em] text-ink-muted">{goalKindLabel[kind]}</h3><div className="space-y-2">{matching.map((goal) => <button key={goal.id} type="button" disabled={disabled} onClick={() => void select(goal.id)} className="w-full rounded-xl border border-line bg-raised/35 px-4 py-3 text-left transition-colors hover:border-accent-edge hover:bg-accent-wash disabled:cursor-not-allowed disabled:opacity-45"><span className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1"><span className="font-medium">{goal.title}</span><span className="text-xs text-accent-ink">继续这个目标 · {status[goal.status] ?? goal.status}</span></span>{goal.long_term_direction && <span className="mt-1 block text-xs leading-5 text-ink-muted">方向：{goal.long_term_direction}</span>}</button>)}</div></section>; })}
      {enabled && m1 && <><div className="my-6 flex items-center gap-3"><span className="h-px flex-1 bg-line" /><span className="text-xs text-ink-faint">或</span><span className="h-px flex-1 bg-line" /></div><button type="button" disabled={disabled} onClick={onDiscussNew} className="primary-action min-h-12 w-full rounded-xl px-4 font-semibold disabled:opacity-45">和 Agent 讨论新目标</button><p className="mt-3 text-sm leading-6 text-ink-muted">一个想法不会被自动创建成目标。Agent 会先协助你澄清活动和方向，等你明确选择后才保存草稿。</p></>}
    </div>
  </dialog>;
}
