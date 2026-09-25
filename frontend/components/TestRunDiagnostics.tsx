import type { DiagnosticSnapshot, TestRun } from "@/lib/adminEvaluations";

const sectionClass = "rounded-2xl border border-line bg-panel p-4";
const labelClass = "mb-3 text-sm font-medium";
const stageLabels: Record<string, string> = {
  state_load: "读取模块与业务记录", router_pre_reply: "生成前 Router", retrieval: "知识检索",
  knowledge_mediator: "知识中介", main_generation: "主回复生成", answer_validator: "回复检查",
  response_display: "展示回复", clinical_extraction: "事实抽取", database_write: "写入数据库",
  workflow_commit: "确认与业务状态提交", pre_reply_extraction: "生成前证据刷新",
  transition_evidence: "用户业务事件识别", confirmation: "确认提交",
  extraction: "事实抽取", risk_gate: "风险判断",
  wait_previous_background: "等待上一轮后台任务",
};
const statusLabels: Record<string, string> = {
  completed: "完成", passed: "通过", running: "进行中", pending: "等待中", skipped: "跳过",
  failed: "失败", error: "失败", timeout: "超时", retrying: "重试中", rejected: "未采纳",
  stale_discarded: "丢弃过期任务", corrected: "已修改", blocked: "已拦截", review: "需复核",
};
const findingLabels: Record<string, string> = {
  empty_reply: "回复为空", oversized_reply: "回复过长", unknown_evidence_id: "引用不在本轮证据中",
  premature_plan: "可能过早制定计划", confirmation_claim: "需确认是否获得用户同意",
  goal_replacement: "可能替换既定目标", completion_claim: "需核对实际完成记录",
  clinical_claim_needs_review: "未经证据的诊断或用药表述", guaranteed_outcome: "过度承诺情绪改善",
  shaming_prescription: "命令或羞辱式建议", unsupported_behavior_label: "缺少依据的行为归因",
  m3_analysis_boundary: "记录阶段越界做行为分析", uncommitted_workflow_claim: "未经后台确认的保存或切换声明",
  panel_confirmation_instruction: "错误引导到面板确认目标",
};
const duration = (value: number | null | undefined) =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${value} ms` : "未记录";
const moduleLabel = (value?: string | null) => value?.replace(/^module_/, "M") || "未记录";
const abnormal = (value?: DiagnosticSnapshot) => Boolean(value && (value.error || value.error_code ||
  ["failed", "error", "timeout", "rejected", "stale_discarded"].includes(value.status ?? "")));

export function RunTimingSummary({ metrics }: { metrics: TestRun["metrics"] }) {
  return <dl aria-label="运行耗时" className="flex flex-wrap gap-x-8 gap-y-2 border-b border-line px-5 py-3 text-sm">
    <div><dt className="text-xs text-ink-faint">本次运行总耗时</dt><dd className="mt-1 tabular-nums">{duration(metrics.duration_ms)}</dd></div>
    <div><dt className="text-xs text-ink-faint">{metrics.telemetry?.first_visible_measurement === "server_sse_release" ? "服务端首次发送可见回复的等待时间" : "用户首次看到回复的等待时间"}</dt><dd className="mt-1 tabular-nums">{duration(metrics.telemetry?.time_to_first_visible_content_ms)}</dd></div>
  </dl>;
}

export default function TestRunDiagnostics({ metrics }: { metrics: TestRun["metrics"] }) {
  const telemetry = metrics.telemetry;
  const validation = telemetry?.answer_validator;
  const router = telemetry?.router_pre_reply;
  const timeline = [...(telemetry?.execution_timeline ?? [])].sort((a, b) => {
    const start = (value?: number | null) => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : Infinity;
    return start(a.start_ms) - start(b.start_ms);
  });
  const changed = Boolean(validation && (
    (typeof validation.original_reply === "string" && typeof validation.replacement_reply === "string" && validation.original_reply !== validation.replacement_reply)
    || validation.replacement_source));
  const original = !changed && validation?.display_source === "original_model_reply";
  return <div className="space-y-4" aria-label="本轮运行诊断">
    <section className={sectionClass}>
      <h4 className={labelClass}>实际调用顺序</h4>
      {timeline.length ? <ol className="space-y-3">{timeline.map((entry, index) => <li key={`${entry.stage}-${index}`} className="border-l-2 border-line pl-3 text-xs leading-6">
        <p className="font-medium">{stageLabels[entry.stage] ?? entry.stage} · {statusLabels[entry.status ?? ""] ?? entry.status ?? "结果未记录"}</p>
        <p className="text-ink-muted">开始偏移：{duration(entry.start_ms)} · 耗时：{duration(entry.duration_ms)}</p>
        {entry.after_display === true && <p className="text-ink-muted">回复展示后继续处理</p>}
        {typeof entry.retry_count === "number" && <p>重试次数：{entry.retry_count}</p>}
        {entry.reason && <p className="break-words text-ink-muted">{entry.reason}</p>}
      </li>)}</ol> : <p className="text-xs text-ink-faint">此记录没有实际调用时间线，不能据此还原先后顺序。</p>}
      <p className="mt-3 text-xs text-ink-faint">各步骤可重叠执行，步骤耗时不相加推算用户等待时间。</p>
    </section>
    <section className={sectionClass}>
      <h4 className={labelClass}>Router 与 Prompt 来源</h4>
      {router ? <dl className="space-y-2 text-xs leading-6">
        <div><dt className="text-ink-faint">Router 选定模块 / 数据库当前模块</dt><dd>{moduleLabel(router.selected_module)} / {moduleLabel(router.database_module)}</dd></div>
        <div><dt className="text-ink-faint">Router 输入类别</dt><dd>{router.input_sources?.length ? router.input_sources.join("、") : "未记录"}</dd></div>
        {router.status && <div><dt className="text-ink-faint">结果</dt><dd>{statusLabels[router.status] ?? router.status}</dd></div>}
        {router.reason && <div><dt className="text-ink-faint">原因</dt><dd className="break-words">{router.reason}</dd></div>}
        {typeof router.retry_count === "number" && <div><dt className="text-ink-faint">重试次数</dt><dd>{router.retry_count}</dd></div>}
      </dl> : <p className="text-xs text-ink-faint">此记录没有生成前 Router 数据。</p>}
      {telemetry?.prompt_sources?.length ? <ul className="mt-3 space-y-2 text-xs leading-6">{telemetry.prompt_sources.map((item, index) =>
        <li key={`${item.source}-${index}`} className="break-all"><span className="text-ink-faint">{item.source}</span> · 版本：{item.version || "未记录"}</li>)}</ul>
        : <p className="mt-3 text-xs text-ink-faint">未记录各段 Prompt 来源与版本{telemetry?.prompt_version ? `；旧记录仅有组合指纹：${telemetry.prompt_version}` : ""}。</p>}
    </section>
    <section className={sectionClass}>
      <h4 className={labelClass}>回答校验 · 共享 Validator</h4>
      {validation ? <>
        <p className="text-sm text-accent-ink">{{passed: "程序校验通过（不等于语义正确）", review: "需人工复核", blocked: "已拦截", corrected: "回复已修改", disabled: "未启用"}[validation.status] ?? validation.status}</p>
        <p className="mt-2 text-xs text-ink-faint">{validation.version || "版本未记录"} · {duration(validation.duration_ms)}{typeof validation.llm_calls === "number" ? ` · 额外 LLM 调用：${validation.llm_calls}` : ""}</p>
        {validation.progression_held === true && <p className="mt-2 text-xs">本轮暂停流程推进</p>}
        {validation.progression_held === false && <p className="mt-2 text-xs">回复检查未暂停流程推进；是否实际迁移以业务状态为准。</p>}
        {validation.findings?.map((finding, index) => <p key={`${finding.code}-${index}`} className="mt-2 rounded-lg bg-raised p-2 text-xs">
          {finding.severity === "block" ? "拦截" : "复核"} · {findingLabels[finding.code] ?? finding.code}
        </p>)}
      </> : <p className="text-xs text-ink-faint">此记录没有校验数据。旧记录不追溯重评，可重新运行用例。</p>}
      {changed ? <div className="mt-3 space-y-3 border-t border-line pt-3 text-xs">
        <p>修改来源：{validation?.replacement_source || "未记录"}；原因：{validation?.reason || validation?.findings?.map(finding => findingLabels[finding.code] ?? finding.code).join("、") || "未记录"}</p>
        <div><h5 className="mb-1 font-medium">模型生成的原文</h5><pre className="whitespace-pre-wrap break-words rounded-lg bg-raised p-3">{validation?.original_reply === "" ? "（空回复）" : validation?.original_reply ?? "未记录"}</pre></div>
        <div><h5 className="mb-1 font-medium">用户实际看到的文字</h5><pre className="whitespace-pre-wrap break-words rounded-lg bg-raised p-3">{validation?.replacement_reply === "" ? "（空回复）" : validation?.replacement_reply ?? "未记录"}</pre></div>
      </div> : <p className="mt-3 text-xs text-ink-muted">{original ? "模型回复原样展示" : "此记录未提供回复是否改写的明确信息。"}</p>}
    </section>
    {telemetry?.reply_trace && <details className={sectionClass}>
      <summary className="cursor-pointer text-sm">原始输出与展示结果</summary>
      <p className="mt-3 text-xs text-ink-faint">展示来源：{telemetry.reply_trace.display_source}</p>
      {([
        ["模型原始输出", telemetry.reply_trace.raw_model_reply],
        ["格式整理后的回复", telemetry.reply_trace.normalized_reply],
        ["实际展示内容", telemetry.reply_trace.visible_reply],
      ] as const).map(([label, value]) => <div key={label} className="mt-3">
        <h5 className="text-xs font-medium">{label}</h5>
        <pre className="mt-2 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-raised p-3 text-xs">{value || "（空回复）"}</pre>
      </div>)}
    </details>}
    {telemetry?.main_input && <details className={sectionClass}>
      <summary className="cursor-pointer text-sm">主模型实际输入</summary>
      <pre className="mt-3 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-raised p-3 text-xs">{JSON.stringify(telemetry.main_input, null, 2)}</pre>
    </details>}
    <details className={sectionClass} open={abnormal(telemetry?.pre_reply_extraction) || abnormal(telemetry?.extraction) || abnormal(telemetry?.database_write)}>
      <summary className="cursor-pointer text-sm">抽取与写库：异常排查 / 测试核对</summary>
      <p className="mt-3 text-xs text-ink-faint">抽取用于整理待保存事实，不是生成多个候选回复。</p>
      {telemetry?.pre_reply_extraction || telemetry?.extraction || telemetry?.database_write ? <pre className="mt-3 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-raised p-3 text-xs">{JSON.stringify({ pre_reply_extraction: telemetry.pre_reply_extraction, extraction: telemetry.extraction, database_write: telemetry.database_write }, null, 2)}</pre>
        : <p className="mt-3 text-xs text-ink-faint">此记录没有抽取与写库明细。</p>}
    </details>
  </div>;
}
