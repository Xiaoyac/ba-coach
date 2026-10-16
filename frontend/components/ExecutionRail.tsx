"use client";

import { useLayoutEffect, useRef, useState } from "react";
import type { ExecutionStage } from "@/lib/conversations";
import styles from "./ExecutionRail.module.css";

const STATUS: Record<ExecutionStage["status"], string> = {
  running: "运行中", retrying: "重新连接中", publishing: "等待发布", completed: "已完成",
  failed: "运行失败", cancelled: "已中断", skipped: "已跳过",
};
const ACTIVE = new Set(["running", "retrying", "publishing"]);

function Station({ stage, animate }: { stage: ExecutionStage; animate: boolean }) {
  const active = ACTIVE.has(stage.status);
  const elapsed = active ? stage.elapsed_ms : stage.duration_ms;
  const precision = ["query_embedding", "knowledge_search", "knowledge_reranker"].includes(stage.stage) ? 2 : 1;
  const failure = stage.failure;
  return <li className={styles.station} data-stage={stage.stage} data-status={stage.status}>
    <div className={styles.node} aria-hidden="true">{stage.status === "completed" ? "✓" : stage.status === "failed" ? "!" : stage.status === "cancelled" ? "×" : stage.status === "skipped" ? "–" : <span />}</div>
    <div className={styles.content}>
      <div className={styles.heading}><span>{stage.label}</span><span className={styles.status}>{!animate && active ? `记录时${STATUS[stage.status]}` : STATUS[stage.status]}</span></div>
      <div className={styles.meta}><span>{stage.model || "处理步骤"}</span><span>{elapsed == null ? "—" : `${(elapsed / 1000).toFixed(precision)} 秒`}</span></div>
      {stage.stage === "retrieval" && <p className={styles.detail}>包含下方向量化、搜索和重排；不要重复累加。</p>}
      {stage.detail && <p className={styles.detail}>{stage.detail}</p>}
      {stage.calls > 1 && <p className={styles.detail}>已发起 {stage.calls} 次调用</p>}
      {stage.status === "failed" && <details className={styles.failure}>
        <summary>查看失败原因 <span aria-hidden="true">⌄</span></summary>
        <div className={styles.failureBody}>
          <p>{failure?.reason || "该阶段未成功完成，历史记录未保留具体失败原因。"}</p>
          {stage.detail && <p>{stage.detail}</p>}
          <dl><dt>错误码</dt><dd>{failure?.code || "未记录"}</dd>
            {failure?.http_status && <><dt>HTTP 状态</dt><dd>{failure.http_status}</dd></>}
            {failure?.request_id && <><dt>请求编号</dt><dd>{failure.request_id}</dd></>}
          </dl>
          <p className={styles.hint}>{failure?.suggestion || "请结合该轮服务端日志排查。"}</p>
        </div>
      </details>}
    </div>
  </li>;
}

export default function ExecutionRail({ stages, parallel = false, animate = true }: { stages: ExecutionStage[]; parallel?: boolean; animate?: boolean }) {
  const list = useRef<HTMLOListElement>(null);
  const [position, setPosition] = useState<number | null>(null);
  const current = stages.find(stage => ACTIVE.has(stage.status));
  // Move only when observed progress changes; no timer simulates completion.
  useLayoutEffect(() => {
    const root = list.current;
    if (!root || parallel || !animate) { setPosition(null); return; }
    const measure = () => {
      const row = Array.from(root.children).find(el => el.getAttribute("data-stage") === current?.stage) as HTMLElement | undefined;
      setPosition(row ? row.offsetTop + 16 : null);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    Array.from(root.children).forEach(el => observer.observe(el));
    return () => observer.disconnect();
  }, [current?.stage, stages.length, parallel, animate]);
  if (!stages.length) return null;
  return <section className={`${styles.route} ${parallel ? styles.branch : ""}`} data-static={!animate} aria-label={parallel ? "并行支线" : "主回复路线"}>
    <div className={styles.routeHeading}><span className={styles.routeBadge}>{parallel ? "支线" : "主线"}</span><h3>{parallel ? "独立并行任务" : "主回复流程"}</h3></div>
    {parallel && <p className={styles.routeHint}>各自运行，不等待前一站</p>}
    <div className={styles.track}>
      <ol ref={list}>{stages.map(stage => <Station key={stage.stage} stage={stage} animate={animate} />)}</ol>
      {animate && !parallel && position !== null && <div className={styles.cursor} style={{ top: position }} data-motion={current?.status} aria-hidden="true"><span /></div>}
    </div>
  </section>;
}
