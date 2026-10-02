"use client";

import { useEffect, useState } from "react";
import type { ChatMessage, MessageRequests, ModelRequestInfo } from "@/lib/api";
import { fetchMessageRequests } from "@/lib/conversations";

const stages: Record<string, string> = {
  pa_tool_continuation: "PA 工具调用后续回复",
  main_generation: "主回复", main_generation_recovery: "主回复重试",
  module_router: "模块路由", module_router_original: "Router 首次判断",
  module_router_recovery: "Router 重试", risk_gate: "风险检查",
  knowledge_mediator: "知识中介", clinical_extraction: "信息抽取",
  module_summarizer: "对话摘要", memory_summary: "记忆总结",
};
function time(value?: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return "未记录";
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

export default function RequestRecordDetails({ message, snapshot, pending }: {
  message: ChatMessage; snapshot: boolean; pending: boolean;
}) {
  const [loaded, setLoaded] = useState<MessageRequests | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [copied, setCopied] = useState<string | null>(null);
  const [copyFailed, setCopyFailed] = useState(false);
  const isSnapshot = snapshot;
  useEffect(() => {
    setLoaded(null); setError(false); setCopied(null); setCopyFailed(false);
    if (isSnapshot || !message.id || pending) return;
    const controller = new AbortController();
    fetchMessageRequests(message.id, controller.signal).then(data => {
      if (!controller.signal.aborted) setLoaded(data);
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [message.id, isSnapshot, pending, retry]);
  // A snapshot never uses its local message ID to fetch private live records.
  const data = isSnapshot ? message.request_records : loaded;
  if (isSnapshot && !data) return <p>本条回复未保存请求记录，分享中无法补填。</p>;
  if (!isSnapshot && (pending || !message.id)) return <p role="status">{pending ? "生成中，保存回复后可查看请求记录。" : "回复尚未同步，请稍后查看。"}</p>;
  if (error) return <p role="alert">请求记录加载失败，请确认管理员权限。
    <button type="button" onClick={() => setRetry(v => v + 1)} className="ml-2 min-h-9 px-2 underline">重试</button></p>;
  if (!data) return <p role="status">正在加载请求记录…</p>;
  async function copy(value: string) {
    try {
      let success = false;
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(value);
          success = true;
        }
      } catch { /* Embedded browsers may need the selection fallback. */ }
      if (!success) {
        const field = document.createElement("textarea");
        field.value = value;
        field.style.position = "fixed";
        field.style.opacity = "0";
        document.body.appendChild(field);
        field.select();
        try { success = document.execCommand("copy"); } finally { field.remove(); }
      }
      if (!success) throw new Error("Copy unavailable");
      setCopied(value); setCopyFailed(false);
    } catch { setCopyFailed(true); }
  }
  const mainRequests = data.requests.filter(entry => entry.stage === "main_generation" || entry.stage === "main_generation_recovery");
  const otherRequests = data.requests.filter(entry => entry.stage !== "main_generation" && entry.stage !== "main_generation_recovery");
  function requestCard(entry: ModelRequestInfo, index: number) {
    const main = entry.stage === "main_generation" || entry.stage === "main_generation_recovery";
    const label = stages[entry.stage] ?? entry.stage;
    return <div key={`${entry.stage}-${entry.request_id}-${index}`} className={`space-y-1 rounded-lg p-3 ${main ? "border border-accent-edge bg-accent-wash" : "bg-surface/70"}`}>
      <p className="font-medium text-ink">{label}{main ? "模型" : ""}</p>
      <p>模型：{entry.model ?? "未记录"}</p>
      {entry.provider && <p>服务：{entry.provider}</p>}
      <p>{label} Request ID：<code className="select-text break-all">{entry.request_id ?? "未记录"}</code></p>
      {entry.request_id && <button type="button" onClick={() => void copy(entry.request_id!)} aria-label={`复制${label}请求 ID`} className="min-h-9 rounded-lg border border-line px-3 hover:bg-accent-wash">
        {copied === entry.request_id ? "已复制" : `复制${label} Request ID`}</button>}
      <p>记录时间：{time(entry.recorded_at)}</p>
      <p>调用耗时：{entry.duration_ms == null ? "未记录" : `${(entry.duration_ms / 1000).toFixed(2)} 秒`}</p>
      {entry.error_code && <p>异常：{entry.error_code}</p>}
    </div>;
  }
  return <div className="space-y-3">
    <p className="font-medium text-accent-ink">请求记录 · 北京时间（UTC+08:00）</p>
    <div><p>用户发送：{time(data.user_sent_at)}</p><p>助手回复：{time(data.assistant_created_at)}</p></div>
    {!data.requests.length && <p>本条回复没有模型请求记录（例如系统开场白）。</p>}
    {mainRequests.map(requestCard)}
    {!data.requests.some(entry => entry.stage === "main_generation") && data.requests.length > 0 && <p>本条回复未保存主回复模型的首次请求记录，无法提供首次主回复 Request ID。</p>}
    {otherRequests.length > 0 && <details className="rounded-lg border border-line p-3">
      <summary className="cursor-pointer text-ink-muted">其他节点的请求记录（{otherRequests.length}）</summary>
      <div className="mt-3 space-y-3">{otherRequests.map(requestCard)}</div>
    </details>}
    <p role="status" className={copyFailed ? "text-alert-ink" : "sr-only"}>
      {copyFailed ? "浏览器未允许复制，请选中完整 ID 手动复制。" : copied ? "请求 ID 已复制" : ""}
    </p>
    <p className="text-ink-faint">每个节点分别调用模型，ID 不同。这里只显示已准确关联到本轮的记录。记录时间是后台保存时间；旧记录或服务商未返回 ID 时显示“未记录”。</p>
    {!isSnapshot && <button type="button" onClick={() => setRetry(v => v + 1)} className="min-h-9 px-2 underline">刷新请求记录</button>}
  </div>;
}
