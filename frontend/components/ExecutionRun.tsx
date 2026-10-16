"use client";

import type { ConversationExecution } from "@/lib/conversations";
import ExecutionRail from "./ExecutionRail";

const STATE_TEXT: Record<ConversationExecution["state"], string> = {
  empty: "发送消息后开始记录", running: "正在处理这一轮", background: "正文已结束，后台继续处理",
  completed: "这一轮已完成", completed_with_errors: "正文已完成，部分环节异常",
  failed: "这一轮出现异常", cancelled: "这一轮已中断", incomplete: "未记录到完整回复",
};

export default function ExecutionRun({ data, error, measured, waiting = false, elapsed = 0, history = false }: {
  data: ConversationExecution | null; error: string | null; measured?: number | null;
  waiting?: boolean; elapsed?: number; history?: boolean;
}) {
  return <section aria-label={history ? "历史运行详情" : "当前流程"} className="space-y-4">
    <section aria-label="正文首字等待时间" className="rounded-xl border border-line bg-raised p-4">
      <p className="text-xs text-ink-muted">点击发送 → 正文首字</p>
      <p className="mt-2 flex items-baseline gap-1.5 text-ink" data-first-body-timing>
        <span className="text-2xl font-medium tabular-nums tracking-tight">{measured != null ? (measured / 1000).toFixed(2) : waiting ? (elapsed / 1000).toFixed(1) : "—"}</span>
        {(measured != null || waiting) && <span className="text-xs text-ink-muted">秒</span>}
      </p>
      <p className="mt-2 text-[11px] leading-relaxed text-ink-muted">{measured != null ? "客户端实测 · 包含网络传输与页面显示等待" : waiting ? "正在等待正文首字显示…" : !data && !error ? "正在读取耗时…" : "本轮未记录客户端耗时"}</p>
      <p className="mt-1 text-[11px] text-ink-faint">承接语不计为正文首字。</p>
    </section>
    <div>
      <p className="text-sm font-medium" role="status">{data ? history && ["running", "background"].includes(data.state) ? "历史状态快照" : STATE_TEXT[data.state] : error ? "状态暂不可用" : "正在读取状态…"}</p>
      {!history && data?.module && <p className="mt-1 text-xs text-ink-muted">当前对话位置：{data.module.replace("module_", "模块 ")}</p>}
      {error && <p className="mt-2 text-xs text-alert-ink" role="alert">{error}</p>}
    </div>
    <div key={`${data?.session_id}:${data?.user_message_id ?? "empty"}`}>
      <ExecutionRail stages={data?.stages.filter(stage => stage.lane === "foreground") ?? []} animate={!history} />
      <ExecutionRail stages={data?.stages.filter(stage => stage.lane === "parallel") ?? []} parallel animate={!history} />
    </div>
    {data && !data.stages.length && <p className="py-5 text-center text-sm text-ink-muted">{data.state === "empty" ? "发出消息后，各环节会显示在这里。" : "这一轮暂无模型运行记录。"}</p>}
    <p className="text-[11px] leading-relaxed text-ink-muted">{history ? "此处为所选轮次的记录，不播放运行动画。" : "圆点随实际状态前进，耗时不代表完成百分比。仅显示已记录的站点；支线与主线可同时运行。"}</p>
  </section>;
}
