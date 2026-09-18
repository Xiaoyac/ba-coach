"use client";

import { useId, useState } from "react";
import type { ChatMessage } from "@/lib/api";
import KnowledgeReferenceDetails from "@/components/KnowledgeReferenceDetails";

function durationLabel(value: number | null | undefined, pending: boolean) {
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) return `${(value / 1000).toFixed(1)} 秒`;
  return pending ? "记录中" : "未记录";
}

/** Separate disclosures; switching channels never mixes their text or model. */
export default function ReasoningDetails({ message, replyPending, routingPending }: {
  message: ChatMessage;
  replyPending: boolean;
  routingPending: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [active, setActive] = useState<"reply" | "router" | "knowledge" | "mediator" | null>(null);
  const id = useId();
  const reply = message.reasoning_content?.trim() ?? "";
  const router = message.routing_reasoning_content?.trim() ?? "";
  const hasReasoning = !!(reply || router || routingPending || (replyPending && message.content));
  if (!hasReasoning && !message.content) return null;

  const channels = [
    { key: "reply", label: "回复深度思考", text: reply, model: message.model_name,
      pending: replyPending, empty: replyPending ? "回复仍在生成，思考内容尚未返回。" : "本轮回复模型未提供独立的思考内容。" },
    { key: "router", label: "路由深度思考", text: router, model: message.router_model_name,
      pending: routingPending && !router,
      empty: routingPending ? "回复已完成，路由正在后台判断，请稍候。" : replyPending ? "回复完成后才会进行路由判断。" : "本轮尚无可查看的路由思考记录。" },
  ] as const;

  return (
    <div className={message.content ? "mt-3 pt-1" : "mt-1"}>
      <button type="button" aria-expanded={expanded} aria-controls={`${id}-debug-details`}
        onClick={() => setExpanded(value => !value)}
        className="inline-flex min-h-9 items-center gap-2 rounded-lg px-1.5 py-1.5 text-[0.76rem] font-medium text-ink-muted transition-colors hover:bg-accent-wash hover:text-accent-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-edge">
        <svg viewBox="0 0 12 12" aria-hidden="true" className={`h-3 w-3 fill-current transition-transform motion-reduce:transition-none ${expanded ? "rotate-180" : ""}`}><path d="M2 4h8L6 8z" /></svg>
        <span>调试详情</span>
      </button>
      <div id={`${id}-debug-details`} hidden={!expanded}>
      <div className="mt-1 grid grid-cols-2 gap-1.5 sm:flex sm:flex-wrap">
        {channels.map(channel => (
          <button key={channel.key} id={`${id}-${channel.key}-button`} type="button"
            onClick={() => setActive(value => value === channel.key ? null : channel.key)}
            aria-expanded={active === channel.key} aria-controls={`${id}-${channel.key}-panel`}
            className={`inline-flex min-h-10 min-w-0 items-center gap-1.5 rounded-lg px-2 py-2 text-left text-[0.76rem] font-medium tracking-normal transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-edge ${active === channel.key ? "bg-accent-wash text-accent-ink" : "text-ink-muted hover:bg-accent-wash hover:text-accent-ink"}`}>
            <span>{active === channel.key ? "收起" : "查看"}{channel.label}</span>
            {channel.pending && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-label="生成中" />}
          </button>
        ))}
        {([{key: "knowledge", label: "对话参考 chunk"}, {key: "mediator", label: "中介节点深度思考"}] as const).map(channel =>
          <button key={channel.key} id={`${id}-${channel.key}-button`} type="button"
            onClick={() => setActive(value => value === channel.key ? null : channel.key)}
            aria-expanded={active === channel.key} aria-controls={`${id}-${channel.key}-panel`}
            className={`inline-flex min-h-10 min-w-0 items-center rounded-lg px-2 py-2 text-left text-[0.76rem] font-medium tracking-normal transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-edge ${active === channel.key ? "bg-accent-wash text-accent-ink" : "text-ink-muted hover:bg-accent-wash hover:text-accent-ink"}`}>
            {channel.label}
          </button>)}
      </div>
      {(["knowledge", "mediator"] as const).map(view => <section key={view} id={`${id}-${view}-panel`} role="region" aria-labelledby={`${id}-${view}-button`}
        hidden={active !== view}
        className="zen-scroll mt-2 max-h-96 overflow-y-auto rounded-xl bg-panel/60 px-3 py-3 text-[0.78rem] font-normal leading-[1.75] tracking-normal text-ink-muted [overflow-wrap:anywhere]">
        {expanded && active === view && <KnowledgeReferenceDetails messageId={message.id} pending={replyPending} view={view} />}
      </section>)}
      {channels.map(channel => (
        <section key={channel.key} id={`${id}-${channel.key}-panel`} role="region"
          aria-labelledby={`${id}-${channel.key}-button`} hidden={active !== channel.key}
          className="zen-scroll mt-2 max-h-72 overflow-y-auto rounded-xl bg-panel/60 px-3 py-3 text-[0.78rem] font-normal leading-[1.75] tracking-normal text-ink-muted whitespace-pre-wrap [overflow-wrap:anywhere]">
          {active === channel.key && <>
            <p className="mb-2 text-[0.72rem] font-medium text-accent-ink">{channel.label}</p>
            <div className="mb-3 text-[0.72rem] leading-relaxed text-ink-muted">
              {channel.key === "reply" ? <>
                <p>思考阶段耗时（估算）：{durationLabel(message.timing?.reply_thinking_ms, replyPending)}</p>
                <p>回复总耗时：{durationLabel(message.timing?.reply_generation_ms, replyPending)}</p>
                <p className="mt-1 text-ink-faint">思考阶段按首个思考片段到首个正文片段的间隔估算，不含前置风险检查。</p>
              </> : <>
                <p>路由处理耗时：{durationLabel(message.timing?.router_processing_ms, channel.pending)}</p>
                <p className="mt-1 text-ink-faint">包含字段提取和模块判断，并非纯思考时间。</p>
              </>}
            </div>
            <div>{channel.text || <span role="status">{channel.empty}</span>}</div>
            {channel.model && <p className="mt-2 text-[0.7rem] text-ink-faint">模型：{channel.model}</p>}
          </>}
        </section>
      ))}
      </div>
    </div>
  );
}
