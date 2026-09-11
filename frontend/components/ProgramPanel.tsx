"use client";
import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { apiHeaders, checkAuthentication } from "@/lib/http";

type Goal = { id: string; title: string; status: string };
type Program = { enabled: boolean; runtime?: { current_module: string; active_goal_id: string | null;
  active_cycle_id: string | null; row_version: number; flow_status: string };
  goals?: Goal[]; draft?: Record<string, unknown> | null; record_hash?: string; can_confirm?: boolean; m1_reusable?: boolean; missing_fields?: string[] };
const labels: Record<string, string> = {
  chief_complaint: "希望改善的问题", functional_chain_summary: "问题理解", trigger_situation: "典型情境",
  event_experience: "具体经历", coping_behavior: "应对方式", coping_consequence: "应对后的影响",
  distress_duration: "持续时间", distress_frequency: "出现频率", attempted_relief_methods: "尝试过的方法",
  exception_positive_scene: "例外与积极经历", activity_content: "准备做什么", schedule_text: "何时执行",
  location: "地点", duration_minutes: "时长（分钟）", frequency_rule: "执行频率", companion: "一起行动的人", core_values: "在意的价值",
  core_values_impact: "与价值的关系", potential_barriers: "可能遇到的困难", barrier_coping_plan: "应对计划",
  record_requirement: "记录内容", negotiated_record_plan: "商定的记录方式", feedback_mechanism: "困难反馈方式",
  acceptance_feeling: "对记录的感受", reminder_text: "提醒约定", execution_result: "执行结果",
  phase_a: "执行前", phase_b: "实际行动", phase_c: "行动后的感受", abc_chain_summary: "本次理解",
  core_difficulty_type: "主要困难", difficulty_description: "困难详情", ba_reeducation_content: "本次解释",
  next_coping_strategy: "下一步策略", review_decision: "下一步决定", review_summary: "复盘总结",
};
const nestedLabels: Record<string, string> = { trigger: "触发情境", feeling: "感受", thought: "想法", behavior: "行为",
  consequence: "结果", situation: "情境", state: "状态", action: "实际行动", deviation: "与计划的差异",
  reward: "获得的体验", impact: "影响", barrier: "困难", plan: "应对", text: "约定", physical_state: "身体感受",
  emotion: "情绪", hindering_factors: "阻碍" };
function describe(value: unknown): string {
  if (value == null) return "尚未提及";
  if (Array.isArray(value)) return value.length ? value.map(describe).join("；") : "尚未明确";
  if (typeof value === "object") return Object.entries(value).filter(([k, v]) => k !== "schema_version" && v != null)
    .map(([k, v]) => `${nestedLabels[k] ?? k}：${describe(v)}`).join("；");
  return String(value);
}
const moduleNames: Record<string, string> = { module_1: "理解当前困扰", module_2: "选择与设定目标",
  module_3: "约定记录方式", module_4: "执行与复盘" };
const statuses: Record<string, string> = { draft: "待完善", active: "进行中", paused: "已暂停", completed: "已完成", abandoned: "已结束", replaced: "已更换" };

export default function ProgramPanel({ sessionId, busy, onChanged }: {
  sessionId: string | null; busy: boolean; onChanged: () => Promise<void>;
}) {
  const [data, setData] = useState<Program | null>(null);
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [selected, setSelected] = useState("");
  const [saving, setSaving] = useState(false);
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setData(null); setError(""); setTitle(""); setSelected(""); setChecked(false);
    if (!sessionId) return;
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`${API_BASE}/api/program/${sessionId}`, { headers: apiHeaders(), signal: controller.signal, cache: "no-store" });
        checkAuthentication(response);
        if (response.status === 409 || response.status === 404) { setData(null); return; }
        if (!response.ok) throw new Error("目标进度加载失败，请稍后重试");
        const value: Program = await response.json();
        if (!controller.signal.aborted) {
          setData(value);
          if (value.can_confirm || (value.m1_reusable && !value.runtime?.active_goal_id)) setOpen(true);
        }
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "加载失败"); }
    }
    void load();
    const interval = window.setInterval(() => { if (!document.hidden) void load(); }, 6000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [sessionId, busy]);
  useEffect(() => setChecked(false), [data?.record_hash]);
  async function act(action: string, body: object) {
    if (!sessionId || !data?.runtime) return;
    setSaving(true); setError("");
    try {
      const response = await fetch(`${API_BASE}/api/program/${sessionId}/${action}`, {
        method: "POST", headers: apiHeaders({ json: true }),
        body: JSON.stringify({ ...body, row_version: data.runtime.row_version }),
      });
      checkAuthentication(response);
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "操作失败，请刷新后重试");
      setData(result); setChecked(false); setTitle("");
      await onChanged();
    } catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setSaving(false); }
  }
  if (!data?.enabled || !data.runtime) return null;
  const runtime = data.runtime;
  const current = data.goals?.find(g => g.id === runtime.active_goal_id);
  const available = data.goals?.filter(g => ["draft", "active"].includes(g.status)) ?? [];
  const fields = Object.entries(data.draft ?? {}).filter(([k, v]) => k in labels && v != null);
  const buttonText = { module_1: "确认理解，并愿意进入目标设定", module_2: "确认这个计划",
    module_3: "确认记录方式，开始执行", module_4: "确认复盘及下一步决定" }[runtime.current_module] ?? "确认记录";
  return <section className="z-10 w-full shrink-0 border-b border-line bg-panel px-4 py-3 text-sm sm:px-6" aria-label="我的目标与进度">
    <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-3 text-left">
      <span className="min-w-0"><span className="text-xs text-muted">我的目标 · {moduleNames[runtime.current_module]}</span>
        <span className="mt-1 block truncate font-medium">{current?.title ?? (data.m1_reusable ? "请选择本段聊天的目标" : "先一起了解你的困扰")}</span></span>
      <span className="shrink-0 rounded-full border border-accent-edge px-3 py-1 text-xs text-accent-ink">{data.can_confirm ? "有记录待确认" : open ? "收起" : "查看"}</span>
    </button>
    {open && <div className="mt-3 max-h-[46vh] space-y-4 overflow-y-auto overscroll-contain">
      {!runtime.active_goal_id && data.m1_reusable && <div className="space-y-3 rounded-2xl border border-line p-4">
        <p className="text-xs leading-6 text-muted">可以同时保留多个目标。每段聊天明确选择一个；新建聊天不会结束其他目标。</p>
        {available.length > 0 && <div className="flex flex-wrap gap-2">
          <label className="min-w-0 flex-1"><span className="mb-1 block text-xs text-muted">继续已有目标</span>
            <select aria-label="选择已有目标" value={selected} onChange={e => setSelected(e.target.value)} className="w-full rounded-xl border border-line bg-panel px-3 py-2">
              <option value="">请选择目标</option>{available.map(g => <option key={g.id} value={g.id}>{g.title} · {statuses[g.status]}</option>)}
            </select></label>
          <button disabled={!selected || saving || busy} onClick={() => void act("goal", { goal_id: selected })} className="self-end rounded-xl border border-accent-edge px-4 py-2 disabled:opacity-40">继续此目标</button>
        </div>}
        <div className="flex gap-2"><input aria-label="新目标标题" value={title} maxLength={255} onChange={e => setTitle(e.target.value)}
          placeholder="新目标，例如：晚饭后出去走一走" className="min-w-0 flex-1 rounded-xl border border-line bg-panel px-3 py-2" />
          <button disabled={!title.trim() || saving || busy} onClick={() => void act("goal", { title: title.trim() })} className="shrink-0 rounded-xl bg-accent px-4 py-2 text-white disabled:opacity-40">新建目标</button></div>
      </div>}
      {current && <p className="text-xs text-muted">目标状态：{statuses[current.status]}。讨论其他目标请新建聊天；当前记录不会被覆盖。</p>}
      {runtime.flow_status === "waiting_execution" && <p className="rounded-xl bg-accent-wash p-3">计划和记录方式已确认。尝试后，回来聊聊实际发生了什么。</p>}
      {runtime.flow_status === "completed" && <p className="rounded-xl bg-accent-wash p-3">这个目标已经结束。你可以新建聊天，选择其他目标。</p>}
      {fields.length > 0 && <div className="rounded-2xl border border-line p-4">
        <h3 className="mb-3 font-medium">本次记录草稿</h3><dl className="space-y-3">{fields.map(([key, value]) =>
          <div key={key}><dt className="text-xs text-muted">{labels[key]}</dt><dd className="mt-1 whitespace-pre-wrap break-words leading-6">{
            key === "execution_result" ? ({ 1: "执行成功", 2: "未执行", 3: "执行受阻", 4: "部分完成" }[Number(value)] ?? describe(value)) :
            key === "review_decision" ? ({ 1: "继续原目标", 2: "更换目标", 3: "调整目标", 4: "结束目标" }[Number(value)] ?? describe(value)) : describe(value)
          }</dd></div>)}</dl>
        {!!data.missing_fields?.length && <p className="mt-4 text-sm text-muted">还需要在聊天中明确：{data.missing_fields.map(k => labels[k] ?? k).join("、")}。</p>}
        {data.can_confirm && <div className="mt-4 space-y-3 border-t border-line pt-4">
          <p className="text-xs leading-5 text-muted">如有不准确之处，请先在聊天中告诉教练，等草稿更新后再确认。</p>
          <label className="flex items-start gap-2"><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} className="mt-1" />我已核对以上记录，并同意记录中的下一步安排。</label>
          <button disabled={!checked || saving || busy} onClick={() => void act("confirm", { record_id: data.draft?.id, record_hash: data.record_hash })}
            className="rounded-xl bg-accent px-4 py-2 text-white disabled:opacity-40">{saving ? "正在保存…" : buttonText}</button>
        </div>}
      </div>}
      {error && <p role="alert" className="rounded-xl bg-red-500/10 p-3 text-red-500">{error}</p>}
    </div>}
  </section>;
}
