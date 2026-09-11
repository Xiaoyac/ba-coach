"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CloseMark, PlusMark } from "@/components/icons";
import WorkbenchSelect from "@/components/WorkbenchSelect";
import MessageMarkdown from "@/components/MessageMarkdown";
import styles from "@/components/TestWorkbench.module.css";
import { downloadEvaluation, evaluationRequest, previewEvaluation,
  type ImportPreview, type TestCase, type TestCaseInput, type TestProvider, type TestRun } from "@/lib/adminEvaluations";

const columns = [
  ["case_code", "Case ID"], ["module", "模块"], ["user_type", "用户类型"],
  ["scenario", "测试场景"], ["user_input", "用户起始输入"], ["expected_behavior", "AI 应达到的目标"],
] as const;
const emptyCase = (): TestCaseInput => ({ case_code: "", module: "module_1", user_type: "", scenario: "",
  user_input: "", expected_behavior: "", extra_columns: {} });
const inputClass = "w-full rounded-xl border border-line-strong bg-canvas px-3 py-2 text-sm text-ink outline-none focus:border-accent";
const buttonClass = "min-h-10 rounded-xl border border-line-strong bg-panel px-3.5 py-2 text-xs font-medium text-ink-muted shadow-sm hover:border-accent-edge hover:bg-raised disabled:opacity-40 disabled:cursor-not-allowed";
const primaryClass = `${buttonClass} !border-accent-edge !bg-accent-wash !text-accent-ink`;
const labels: Record<string, string> = { queued: "排队中", running: "运行中", completed: "运行完成", failed: "执行失败", interrupted: "已中断" };
const errorText = (e: unknown) => e instanceof Error ? e.message : String(e);

export default function TestWorkbench({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<"cases" | "runs">("cases");
  const [cases, setCases] = useState<TestCase[]>([]);
  const [runs, setRuns] = useState<TestRun[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [module, setModule] = useState("all");
  const [query, setQuery] = useState("");
  const [runFilter, setRunFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [provider, setProvider] = useState<TestProvider>("deepseek");
  const [editing, setEditing] = useState<TestCase | "new" | null>(null);
  const [draft, setDraft] = useState<TestCaseInput>(emptyCase);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [acceptPartial, setAcceptPartial] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [detail, setDetail] = useState<TestRun | null>(null);
  const [followup, setFollowup] = useState("");
  const [verdict, setVerdict] = useState("unreviewed");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [batch, setBatch] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const alive = useRef(true), stop = useRef(false), batchLock = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    const [c, r] = await Promise.all([
      evaluationRequest<{cases: TestCase[]}>("/cases"),
      evaluationRequest<{runs: TestRun[]; next_offset: number | null}>("/runs"),
    ]);
    if (alive.current) { setCases(c.cases); setRuns(r.runs); setNextOffset(r.next_offset); }
  }, []);

  useEffect(() => {
    alive.current = true;
    const previous = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    void load().catch(e => { if (alive.current) setError(errorText(e)); }).finally(() => { if (alive.current) setLoading(false); });
    return () => { alive.current = false; stop.current = true; previous?.focus(); };
  }, [load]);

  useEffect(() => {
    dialogRef.current?.querySelector<HTMLElement>('[data-workbench-layer] input, [data-workbench-layer] textarea, [data-workbench-layer] select, [data-workbench-layer] button')?.focus();
  }, [editing, preview?.selected_sheet, detail?.id]);

  // Poll persisted state; a closed browser does not own the currently executing turn.
  useEffect(() => {
    if (!runs.some(r => ["queued", "running"].includes(r.status))) return;
    const timer = setTimeout(() => void load().catch(e => setError(errorText(e))), 2500);
    return () => clearTimeout(timer);
  }, [runs, load]);

  async function action(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (e) { if (alive.current) setError(errorText(e)); }
    finally { if (alive.current) setBusy(false); }
  }

  function edit(item: TestCase | "new") {
    setEditing(item);
    setDraft(item === "new" ? emptyCase() : Object.fromEntries(
      [...columns.map(([key]) => [key, item[key]]), ["extra_columns", item.extra_columns]]) as unknown as TestCaseInput);
  }

  async function save() {
    await action(async () => {
      await evaluationRequest(editing === "new" ? "/cases" : `/cases/${(editing as TestCase).id}`,
        editing === "new" ? draft : { ...draft, revision: (editing as TestCase).revision }, editing === "new" ? "POST" : "PUT");
      setEditing(null); await load(); setNotice("用例已保存");
    });
  }

  async function openRun(runId: string) {
    await action(async () => {
      const run = await evaluationRequest<TestRun>(`/runs/${runId}`);
      setDetail(run); setFollowup(""); setVerdict(run.reviews?.[0]?.verdict ?? "unreviewed");
      setNotes(run.reviews?.[0]?.notes ?? "");
    });
  }

  async function execute(caseId: string, parent?: TestRun) {
    const requestId = crypto.randomUUID();
    await evaluationRequest<TestRun>(`/cases/${caseId}/runs`, { request_id: requestId, provider,
      ...(parent ? {parent_run_id: parent.id, message: followup} : {}) }, "POST");
    await load();
    // Bound the wait; a lost response never automatically starts another paid model call.
    for (let attempt = 0; attempt < 110 && alive.current; attempt++) {
      const run = await evaluationRequest<TestRun>(`/runs/${requestId}`);
      if (!["queued", "running"].includes(run.status)) {
        await load(); return run;
      }
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    throw new Error("等待结束，请在测试记录中刷新结果；本次不会自动重试。");
  }

  async function runSelected(ids: string[]) {
    if (batchLock.current || busy || ids.length === 0) return;
    batchLock.current = true; stop.current = false; setBatch(true); setTab("runs"); setError("");
    let done = 0;
    try {
      for (const id of ids.slice(0, 20)) {
        if (stop.current || !alive.current) break;
        setProgress(`正在运行 ${done + 1} / ${Math.min(ids.length, 20)}`);
        await execute(id); done++;
      }
      if (alive.current) setNotice(`已执行 ${done} 条。执行状态和质量结论请在运行记录中查看、评审。`);
    } catch (e) { if (alive.current) setError(errorText(e)); }
    finally { batchLock.current = false; if (alive.current) { setBatch(false); setProgress(""); } }
  }

  function close() { stop.current = true; onClose(); }
  const filtered = cases.filter(c => (module === "all" || c.module === module) &&
    columns.some(([key]) => c[key].toLowerCase().includes(query.toLowerCase())));
  const filteredRuns = runs.filter(r => (module === "all" || r.case_snapshot.module === module) &&
    (runFilter === "all" || (runFilter === "unreviewed" ? r.status === "completed" && (!r.latest_verdict || r.latest_verdict === "unreviewed") : r.status === runFilter)) &&
    `${r.case_snapshot.case_code} ${r.input_text}`.toLowerCase().includes(query.toLowerCase()));

  return <div className="fixed inset-0 z-[75] bg-canvas/95 p-2 backdrop-blur-md sm:p-5">
    <section ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="test-workbench-title"
      onKeyDown={e => {
        if (e.key === "Escape") { if (editing) setEditing(null); else if (preview) setPreview(null); else if (detail) setDetail(null); else close(); }
        if (e.key === "Tab") {
          const scope = e.currentTarget.querySelector<HTMLElement>('[data-workbench-layer]') ?? e.currentTarget;
          const focusable = Array.from(scope.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]')).filter(el => el.getClientRects().length > 0);
          const first = focusable[0], last = focusable.at(-1);
          if (e.shiftKey && (document.activeElement === first || document.activeElement === e.currentTarget)) { e.preventDefault(); last?.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
        }
      }}
      className={`${styles.workbench} relative mx-auto flex h-full max-w-[1800px] flex-col overflow-hidden rounded-[24px] border border-line-strong bg-canvas text-ink shadow-2xl outline-none`}>
      <header className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-4">
        <div className="mr-auto"><p className="mb-1 text-[10px] font-medium uppercase tracking-[.24em] text-accent-ink">BA COACH / EVALUATION</p><h2 id="test-workbench-title" className="text-xl font-semibold tracking-tight">测试工作台</h2>
          <p className="mt-1 text-xs text-ink-faint">让每一次对话测试，都有迹可循。</p></div>
        <button className={buttonClass} disabled={busy} onClick={() => void action(() => load())}>刷新</button>
        <button className={buttonClass} disabled={busy || batch} onClick={() => fileInput.current?.click()}>导入 Excel / CSV</button>
        <button className={buttonClass} disabled={busy} onClick={() => void action(() => downloadEvaluation(tab === "cases" ? "cases.csv" : "results.csv"))}>导出{tab === "cases" ? "用例" : "记录"}</button>
        <button className={buttonClass} onClick={close} aria-label="关闭测试工作台"><CloseMark className="h-4 w-4" /></button>
        <input ref={fileInput} className="hidden" type="file" accept=".xlsx,.csv" onChange={e => {
          const next = e.target.files?.[0]; e.target.value = "";
          if (next) void action(async () => { setFile(next); setAcceptPartial(false); setPreview(await previewEvaluation(next)); });
        }}/>
      </header>
      <div className="hidden grid-cols-4 gap-3 px-5 pt-4 sm:grid">
        {[["用例总数", cases.length, "按模块维护与复用"], ["已选用例", selected.length, "每批最多 20 条"], ["运行中", runs.filter(r => ["queued","running"].includes(r.status)).length, "当前已加载记录"], ["待评审", runs.filter(r => r.status === "completed" && (!r.latest_verdict || r.latest_verdict === "unreviewed")).length, "当前已加载记录"]].map(([label,count,hint]) => <div key={label} className="rounded-2xl border border-line bg-panel px-4 py-3"><p className="text-xs text-ink-faint">{label}</p><p className="mt-1 text-2xl font-semibold tabular-nums">{loading ? "—" : count}</p><p className="mt-1 hidden text-[11px] text-ink-faint sm:block">{hint}</p></div>)}
      </div>
      <div className="flex flex-wrap items-center gap-2 px-5 pt-4 pb-3">
        <div className="flex gap-1 rounded-xl border border-line bg-raised p-1" role="group" aria-label="工作台视图">
        {(["cases", "runs"] as const).map(t => <button key={t} onClick={() => setTab(t)}
          aria-pressed={tab === t} className={`rounded-lg px-4 py-2 text-xs font-medium ${tab === t ? "bg-panel text-accent-ink shadow-sm" : "text-ink-faint hover:text-ink"}`}>{t === "cases" ? `评测集 (${cases.length})` : "测试记录"}</button>)}
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
        <span className="text-xs text-ink-faint">执行模型</span>
        <WorkbenchSelect aria-label="测试模型" disabled={batch || busy} className="w-32" value={provider} onChange={e => setProvider(e.target.value as TestProvider)}>
          <option value="deepseek">DeepSeek</option><option value="doubao">豆包</option><option value="claude">Claude</option>
        </WorkbenchSelect>
        <button className={buttonClass} disabled={busy || batch} onClick={() => edit("new")}><PlusMark className="mr-1 inline h-3 w-3"/>新增用例</button>
        {batch ? <button className={buttonClass} onClick={() => { stop.current = true; setNotice("将在当前用例完成后停止后续运行"); }}>停止后续运行</button> :
          <button className={primaryClass} disabled={busy || selected.length === 0} onClick={() => void runSelected(selected)}>运行选中 ({selected.length}/20)</button>}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-y border-line bg-panel/50 px-5 py-3">
        <input aria-label="搜索用例" className={`${inputClass} !w-full sm:!w-72`} placeholder="搜索编号、场景或用户输入…" value={query} onChange={e => setQuery(e.target.value)}/>
        <WorkbenchSelect aria-label="筛选模块" className="w-36" value={module} onChange={e => setModule(e.target.value)}><option value="all">全部模块</option>{[1,2,3,4].map(n => <option key={n} value={`module_${n}`}>M{n}</option>)}</WorkbenchSelect>
        {tab === "runs" && <WorkbenchSelect aria-label="筛选运行状态" className="w-36" value={runFilter} onChange={e => setRunFilter(e.target.value)}><option value="all">全部状态</option><option value="unreviewed">待评审</option>{Object.entries(labels).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</WorkbenchSelect>}
        {(query || module !== "all" || runFilter !== "all") && <button className={buttonClass} onClick={() => {setQuery("");setModule("all");setRunFilter("all");}}>重置筛选</button>}
        {selected.length > 0 && <button className={buttonClass} onClick={() => setSelected([])}>清空选择</button>}
        <span className="ml-auto text-xs text-ink-faint">{tab === "cases" ? filtered.length : filteredRuns.length} 条结果</span>
      </div>
      {(error || notice || progress) && <p role={error ? "alert" : "status"} className={`px-5 py-2 text-sm ${error ? "text-alert-ink" : "text-accent-ink"}`}>{error || progress || notice}</p>}
      <div className="zen-scroll min-h-0 flex-1 overflow-auto">
        {tab === "cases" ? <table className="w-full min-w-[1200px] border-collapse text-left text-xs">
          <thead className="sticky top-0 z-10 bg-raised"><tr>
            <th className="w-10 border border-line p-3"><input aria-label="选中当前筛选前20条" type="checkbox" checked={filtered.length > 0 && filtered.slice(0,20).every(c => selected.includes(c.id))}
              onChange={e => setSelected(e.target.checked ? filtered.slice(0,20).map(c => c.id) : [])}/></th>
            {columns.map(([key,label]) => <th key={key} className={`border border-line p-3 font-medium ${key === "case_code" ? "w-28" : key === "module" ? "w-16" : "min-w-48"}`}>{label}</th>)}<th className="w-28 border border-line p-3">操作</th>
          </tr></thead><tbody>{filtered.map(c => <tr key={c.id} data-selected={selected.includes(c.id)} className="align-top hover:bg-raised/50">
            <td className="border border-line p-3"><input aria-label={`选择 ${c.case_code}`} type="checkbox" checked={selected.includes(c.id)}
              onChange={e => setSelected(prev => e.target.checked ? [...prev, c.id].slice(0,20) : prev.filter(id => id !== c.id))}/></td>
            {columns.map(([key]) => <td key={key} className={`whitespace-pre-wrap border border-line p-3 leading-relaxed ${key === "expected_behavior" ? "text-accent-ink" : ""}`}>{key === "module" ? c.module.replace("module_","M") : c[key]}</td>)}
            <td className="space-y-2 border border-line p-2"><button className={buttonClass} disabled={busy || batch} onClick={() => edit(c)}>编辑</button>
              <button className={buttonClass} disabled={busy || batch} onClick={() => void runSelected([c.id])}>运行</button></td>
          </tr>)}</tbody></table> : <table className="w-full min-w-[850px] text-left text-xs">
          <thead className="sticky top-0 bg-raised"><tr>{["Case ID / 版本", "模块", "状态", "模型", "耗时", "实际回答", "时间", ""].map((h,i) => <th key={i} className="border border-line p-3">{h}</th>)}</tr></thead>
          <tbody>{filteredRuns.map(r => <tr key={r.id} className="align-top hover:bg-raised/50">
            <td className="border border-line p-3">{r.case_snapshot.case_code} / v{r.case_snapshot.revision}{r.parent_run_id && <span className="block text-ink-faint">追问</span>}</td>
            <td className="border border-line p-3">{r.case_snapshot.module.replace("module_", "M")}</td>
            <td className="border border-line p-3">{labels[r.status] ?? r.status}{r.error_code && <p>{r.error_code}</p>}
              {r.status === "completed" && <p className="mt-1 text-accent-ink">{r.latest_verdict === "pass" ? "评审通过" : r.latest_verdict === "fail" ? "评审未通过" : "待评审"}</p>}</td>
            <td className="border border-line p-3">{r.model ?? r.provider}</td><td className="border border-line p-3">{r.metrics.duration_ms === undefined ? "—" : `${r.metrics.duration_ms} ms`}</td>
            <td className="max-w-sm whitespace-pre-wrap border border-line p-3">{r.reply.slice(0,180) || "等待结果"}{r.reply.length > 180 && "…"}</td>
            <td className="border border-line p-3">{new Date(r.created_at).toLocaleString("zh-CN")}</td>
            <td className="border border-line p-3"><button className={buttonClass} disabled={busy} onClick={() => void openRun(r.id)}>查看 / 评审</button></td>
          </tr>)}</tbody></table>}
        {loading ? <div role="status" className="p-12 text-center text-sm text-ink-faint">正在加载工作台…</div> : ((tab === "cases" && !filtered.length) || (tab === "runs" && !filteredRuns.length)) && <div className="mx-auto max-w-md px-6 py-14 text-center"><div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-accent-edge bg-accent-wash text-xl text-accent-ink">☷</div><h3 className="text-base font-medium">{query || module !== "all" || runFilter !== "all" ? "没有匹配的结果" : tab === "cases" ? "从第一份评测集开始" : "还没有测试记录"}</h3><p className="mt-2 text-sm leading-6 text-ink-faint">{tab === "cases" ? "导入 Excel / CSV 或新增用例，然后选择模型运行。已有内容可尝试清空筛选条件。" : "在评测集中选择用例并运行，回答与评审记录会保存在这里。"}</p></div>}
        {tab === "runs" && nextOffset !== null && <button className={`${buttonClass} m-4`} disabled={busy} onClick={() => void action(async () => {
          const r = await evaluationRequest<{runs: TestRun[]; next_offset: number | null}>(`/runs?offset=${nextOffset}`);
          setRuns(prev => [...prev, ...r.runs.filter(row => !prev.some(p => p.id === row.id))]); setNextOffset(r.next_offset);
        })}>加载更早记录</button>}
      </div>
      <footer className="hidden flex-wrap items-center justify-between gap-2 border-t border-line bg-panel px-5 py-3 text-[11px] text-ink-faint sm:flex"><span>固定模块测试 · 回答自动归档 · 验收目标不发送给模型</span><span>关闭面板停止后续队列，当前任务继续保存</span></footer>

      {editing && <div data-workbench-layer className="absolute inset-0 z-20 flex items-center justify-center bg-canvas/95 p-3"><form className="zen-scroll max-h-full w-full max-w-3xl overflow-auto rounded-2xl border border-line-strong bg-canvas p-5" onSubmit={e => { e.preventDefault(); void save(); }}>
        <h3 className="mb-4 text-lg">{editing === "new" ? "新增测试用例" : `编辑 ${editing.case_code}`}</h3>
        {error && <p role="alert" className="mb-3 text-sm text-alert-ink">{error}</p>}
        <div className="grid gap-4 sm:grid-cols-2">{columns.map(([key,label]) => <label key={key} className={`block text-xs text-ink-muted ${["scenario","user_input","expected_behavior"].includes(key) ? "sm:col-span-2" : ""}`}>{label}
          {key === "module" ? <WorkbenchSelect aria-label={label} className="mt-1 w-full" value={draft.module} onChange={e => setDraft({...draft,module:e.target.value as TestCaseInput["module"]})}>{[1,2,3,4].map(n => <option key={n} value={`module_${n}`}>M{n}</option>)}</WorkbenchSelect> :
            <textarea aria-label={label} className={`${inputClass} mt-1`} required={["case_code","user_input"].includes(key)} rows={key === "case_code" ? 1 : 3}
              maxLength={key === "case_code" ? 64 : key === "user_type" ? 4000 : key === "scenario" ? 6000 : 12000}
              value={draft[key]} onChange={e => setDraft({...draft,[key]:e.target.value})}/>}
        </label>)}</div>
        <p className="mt-3 text-xs text-ink-faint">AI 应达到的目标只用于评审，不发给模型。已有运行保留旧版本。</p>
        <div className="mt-4 flex justify-end gap-2"><button type="button" className={buttonClass} disabled={busy} onClick={() => setEditing(null)}>取消</button><button className={buttonClass} disabled={busy}>保存用例</button></div>
      </form></div>}

      {preview && <div data-workbench-layer className="absolute inset-0 z-20 overflow-auto bg-canvas p-6">
        <h3 className="text-lg">导入预览 · {file?.name}</h3><p className="my-3 text-sm text-ink-muted">{preview.cases.length} 条有效用例，{preview.errors.length} 处错误。确认后统一保存，不覆盖已有 Case ID。</p>
        {error && <p role="alert" className="text-alert-ink">{error}</p>}
        <WorkbenchSelect className="my-3 w-full max-w-sm" aria-label="选择工作表" disabled={busy} value={preview.selected_sheet} onChange={e => void action(async () => { setAcceptPartial(false); if(file) setPreview(await previewEvaluation(file,e.target.value)); })}>
          {preview.sheets.map(sheet => <option key={sheet}>{sheet}</option>)}
        </WorkbenchSelect>
        {preview.errors.length > 0 && <label className="mb-4 flex items-center gap-2 text-sm text-alert-ink"><input type="checkbox" checked={acceptPartial} onChange={e=>setAcceptPartial(e.target.checked)}/>仅导入 {preview.cases.length} 条有效用例；以下 {preview.errors.length} 行不会导入，原文件保持不变</label>}
        <div className="mb-4 flex gap-2"><button className={buttonClass} disabled={busy} onClick={() => setPreview(null)}>取消</button>
          <button className={buttonClass} disabled={busy || !preview.cases.length || (preview.errors.length > 0 && !acceptPartial)} onClick={() => void action(async () => { await evaluationRequest("/import",{cases:preview.cases},"POST"); const message = `已导入 ${preview.cases.length} 条用例${preview.errors.length ? `；${preview.errors.length} 行未导入，请保留原文件继续完善` : ""}`; setPreview(null); await load(); setNotice(message); })}>确认导入</button></div>
        {preview.errors.map(e => <p key={e.row} className="my-2 text-sm text-alert-ink">第 {e.row} 行：{e.message}</p>)}
        {preview.cases.slice(0,20).map(c => <div key={c.case_code} className="border-t border-line py-3"><strong>{c.case_code} · {c.module.replace("module_","M")}</strong><p className="mt-1 whitespace-pre-wrap text-sm text-ink-muted">{c.user_input}</p></div>)}
        {preview.cases.length > 20 && <p>预览显示前 20 条，确认将导入全部 {preview.cases.length} 条。</p>}
      </div>}

      {detail && <div data-workbench-layer className="absolute inset-0 z-20 flex flex-col bg-canvas">
        <header className="flex items-center gap-3 border-b border-line px-5 py-4"><h3 className="mr-auto">{detail.case_snapshot.case_code} · v{detail.case_snapshot.revision} · 运行详情</h3>
          <button className={buttonClass} disabled={busy} onClick={() => void openRun(detail.id)}>刷新结果</button><button className={buttonClass} onClick={() => setDetail(null)}>返回列表</button></header>
        {error && <p role="alert" className="px-5 py-2 text-alert-ink">{error}</p>}
        <div className="zen-scroll grid min-h-0 flex-1 gap-6 overflow-auto p-5 lg:grid-cols-[1.4fr_1fr]">
          <div><h4 className="mb-3 text-sm text-ink-faint">实际对话 · {labels[detail.status]} · {detail.model ?? detail.provider}</h4>
            {(detail.transcript.length ? detail.transcript : [{role:"user",content:detail.input_text}]).map((m,i) => <div key={i} className={`mb-3 rounded-xl border border-line p-4 ${m.role === "user" ? "bg-mine" : "bg-raised"}`}>
              <p className="mb-2 text-xs text-ink-faint">{m.role === "user" ? "测试用户" : "AI"}</p><div className="whitespace-pre-wrap text-sm leading-7">{m.role === "user" ? m.content : <MessageMarkdown text={m.content}/>}</div></div>)}
            {detail.error_code && <p className="text-alert-ink">执行未完成：{detail.error_code}。可返回列表重新运行，用新记录保存结果。</p>}
            <label className="mt-4 block text-sm">继续追问<textarea rows={3} className={`${inputClass} mt-2`} maxLength={12000} placeholder="输入下一轮用户消息，系统自动带上这次测试的上下文" value={followup} onChange={e => setFollowup(e.target.value)}/></label>
            <button className={`${buttonClass} mt-2`} disabled={busy || batch || detail.status !== "completed" || !followup.trim()} onClick={() => void action(async () => {
              const next = await execute(detail.case_id,detail); if(next) {setDetail(next);setFollowup("");setVerdict("unreviewed");setNotes("");}
            })}>{busy ? "正在执行…" : "发送追问并自动记录"}</button>
          </div>
          <div className="space-y-5"><section><h4 className="mb-2 text-sm text-ink-faint">用例场景与验收目标</h4><p className="whitespace-pre-wrap text-sm leading-6">{detail.case_snapshot.user_type}{"\n"}{detail.case_snapshot.scenario}</p>
            <p className="mt-3 whitespace-pre-wrap rounded-xl border border-accent-edge bg-accent-wash p-4 text-sm leading-7 text-accent-ink">{detail.case_snapshot.expected_behavior || "未填写验收目标"}</p></section>
            <section><h4 className="mb-2 text-sm">人工评审</h4><WorkbenchSelect aria-label="评审结论" className="w-full" value={verdict} onChange={e => setVerdict(e.target.value)}><option value="unreviewed">待评审</option><option value="pass">通过</option><option value="fail">未通过</option></WorkbenchSelect>
              <textarea aria-label="评审备注" rows={5} maxLength={12000} className={`${inputClass} mt-2`} placeholder="记录符合/不符合的目标，以及具体回答依据" value={notes} onChange={e => setNotes(e.target.value)}/>
              <button className={`${buttonClass} mt-2`} disabled={busy} onClick={() => void action(async () => {
                await evaluationRequest(`/runs/${detail.id}/reviews`,{verdict,notes},"POST"); setDetail(await evaluationRequest<TestRun>(`/runs/${detail.id}`)); await load(); setNotice("评审已保存");
              })}>保存评审</button>
              {detail.reviews?.map(r => <div key={r.id} className="mt-3 border-t border-line pt-2 text-xs text-ink-muted">{r.verdict === "pass" ? "通过" : r.verdict === "fail" ? "未通过" : "待评审"} · 评审人 #{r.reviewer_id} · {new Date(r.created_at).toLocaleString("zh-CN")}<p className="mt-1 whitespace-pre-wrap">{r.notes}</p></div>)}
            </section>
            <section className="rounded-2xl border border-line bg-panel p-4"><h4 className="mb-3 text-sm font-medium">回答校验 · 共享 Validator</h4>
              {detail.metrics.telemetry?.answer_validator ? <><p className="text-sm text-accent-ink">{{passed:"程序校验通过（不等于语义正确）",review:"需人工复核",blocked:"已拦截",disabled:"未启用"}[detail.metrics.telemetry.answer_validator.status] ?? detail.metrics.telemetry.answer_validator.status}</p><p className="mt-2 text-xs text-ink-faint">{detail.metrics.telemetry.answer_validator.version} · {detail.metrics.telemetry.answer_validator.duration_ms ?? "—"} ms · 无额外 LLM 调用</p>{detail.metrics.telemetry.answer_validator.findings?.map(f => <p key={f.code} className="mt-2 rounded-lg bg-raised p-2 text-xs">{f.severity === "block" ? "拦截" : "复核"} · {({empty_reply:"回复为空",oversized_reply:"回复过长",unknown_evidence_id:"引用不在本轮证据中",premature_plan:"可能过早制定计划",confirmation_claim:"需确认是否获得用户同意",goal_replacement:"可能替换既定目标",completion_claim:"需核对实际完成记录",clinical_claim_needs_review:"需核对诊断或用药相关表述"} as Record<string,string>)[f.code] ?? f.code}</p>)}</> : <p className="text-xs text-ink-faint">此记录没有校验数据。旧记录不追溯重评，可重新运行用例。</p>}
            </section>
            <details className="rounded-2xl border border-line p-4"><summary className="cursor-pointer text-sm text-ink-faint">高级信息：运行指标与检索来源</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-raised p-3 text-xs">{JSON.stringify(detail.metrics,null,2)}</pre></details>
          </div>
        </div>
      </div>}
    </section>
  </div>;
}
