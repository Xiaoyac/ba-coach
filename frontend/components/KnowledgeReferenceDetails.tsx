"use client";

import { useEffect, useState } from "react";
import { fetchKnowledgeReferences, type KnowledgeReferences } from "@/lib/conversations";
import { knowledgeScoreLabel } from "@/lib/knowledgeScores";

const statusLabels: Record<string, string> = {
  returned: "已召回", empty: "未召回相关片段", skipped: "已跳过", disabled: "未启用",
  completed: "已完成", fallback: "未放行", timeout: "中介超时，片段未放行",
  invalid_output_or_provider_error: "中介输出异常或服务错误，片段未放行",
  no_knowledge: "没有待审核片段", no_applicable_evidence: "中介判定片段不适用", guided: "已筛选片段",
  context_too_large: "上下文超过安全处理上限，片段未放行",
  output_truncated: "中介输出被截断，片段未放行",
  empty_output: "中介没有返回有效结果，片段未放行",
  invalid_evidence: "中介引用校验失败，片段未放行",
  invalid_json: "中介结果不是有效 JSON，片段未放行",
  invalid_schema: "中介结果字段不符合契约，片段未放行",
};
const label = (value: string | null) => value ? statusLabels[value] ?? value : "未记录";

export default function KnowledgeReferenceDetails({ messageId, pending, view = "knowledge" }: {
  messageId?: number | null; pending: boolean; view?: "knowledge" | "mediator";
}) {
  const [data, setData] = useState<KnowledgeReferences | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    setData(null); setError(false);
    if (!messageId) return;
    const controller = new AbortController();
    fetchKnowledgeReferences(messageId, controller.signal).then(value => {
      if (!controller.signal.aborted) setData(value);
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [messageId, retry]);

  if (!messageId) return <p role="status">{pending ? "回复生成中，保存后可查看本轮参考片段。" : "本条回复尚无可查看的片段记录；正在同步的回复请稍后重试。"}</p>;
  if (error) return <div role="alert">参考片段加载失败，可能已无访问权限或对话已删除。
    <button type="button" className="ml-2 min-h-9 rounded-lg px-2 text-accent-ink underline focus-visible:ring-2 focus-visible:ring-accent-edge"
      onClick={() => setRetry(value => value + 1)}>重试加载</button></div>;
  if (!data) return <p role="status">正在加载本轮参考片段…</p>;
  if (!data.available) return <div role="status"><p className="font-mono text-ink">null</p><p className="mt-2">本条回复未记录参考片段（历史回复、开场白或未经过知识检索流程）。不会重新检索补填。</p></div>;
  if (!data.recalled.length) return <p role="status" className="font-mono text-ink">null</p>;
  if (view === "mediator") return <div className="space-y-3">
    <div className="space-y-1">
      <p className="font-medium text-accent-ink">中介节点深度思考</p>
      <p>处理状态：{label(data.mediator_status)} · {label(data.mediator_reason)}</p>
      <p>中介处理总耗时：{data.mediator_duration_ms == null ? "未记录" : `${(data.mediator_duration_ms / 1000).toFixed(1)} 秒`}</p>
      <p>总耗时包含请求、生成和解析，不等于纯思考时长。</p>
    </div>
    <p className="whitespace-pre-wrap text-ink [overflow-wrap:anywhere]">{data.mediator_reasoning_content || "null"}</p>
    {!data.mediator_reasoning_content && <p>本轮未返回或未保存独立思考内容；不使用中介建议替代。</p>}
    {data.mediator_model && <p>模型：{data.mediator_model}</p>}
  </div>;

  return <div className="space-y-4">
    <div className="space-y-1">
      <p className="font-medium text-accent-ink">本轮召回 {data.recalled.length} 段 · 传给回复模型 {data.provided.length} 段</p>
      <p>模块：{data.module ?? "未记录"} · 检索：{label(data.retrieval_outcome)}</p>
      {data.gate_reason && <p>意图门控：{label(data.gate_reason)}</p>}
      <p>中介：{label(data.mediator_status)} · {label(data.mediator_reason)}</p>
      {data.context_withheld && <p className="text-accent-ink">安全上下文不可用，本轮片段全部未放行。</p>}
      {data.validator_status && ["blocked", "corrected"].includes(data.validator_status) &&
        <p className="text-accent-ink">回复被校验器拦截或改写；以下仅代表原生成请求的参考材料。</p>}
      <p>“传给回复模型”不代表回复实际引用。检索分数不是准确率或置信度，不同评分方式不能直接比较。</p>
    </div>
    {([
      { title: "传给回复模型的片段", chunks: data.provided, empty: "本轮没有知识库片段传给回复模型。" },
      { title: "检索原始召回（调试）", chunks: data.recalled, empty: "本轮没有召回知识库片段。" },
    ]).map(group => <section key={group.title} className="space-y-2">
      <h4 className="font-medium text-ink">{group.title}</h4>
      {!group.chunks.length && <p>{group.empty}</p>}
      {group.chunks.map((chunk, index) => <details key={`${chunk.id}-${index}`} open
        className="rounded-lg bg-surface/70 px-3 py-2">
        <summary className="cursor-pointer py-1 font-medium text-ink focus-visible:outline-accent-edge">
          {index + 1}. {chunk.source || "未标注来源"}
        </summary>
        <p className="mt-1 text-xs [overflow-wrap:anywhere]">ID：{chunk.id} · {knowledgeScoreLabel(chunk)}</p>
        <p className="mt-2 whitespace-pre-wrap text-ink-muted [overflow-wrap:anywhere]">{chunk.text}</p>
      </details>)}
    </section>)}
  </div>;
}
