"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { apiHeaders, checkAuthentication } from "@/lib/http";

type CacheStats = {
  enabled: boolean; scope: "process"; started_at: string;
  requests: number; hits: number; hit_rate: number | null;
  coalesced: number; entries: number;
  ttl_seconds?: number; empty_ttl_seconds?: number;
  misses?: number; miss_reasons?: Record<string, number>;
  invalidations?: number; saved_model_calls?: number; coalesced_saved_model_calls?: number; uncacheable?: number;
  diagnostics?: Record<string, number>;
  public_preparation?: { hits: number; requests: number; hit_rate: number | null; entries: number };
};

const reasonLabels: Record<string, string> = {
  first_or_untracked: "首次或未追踪的查询", expired: "相同查询已过期", evicted: "容量淘汰",
  invalidated: "知识更新或主动清空", key_changed: "知识版本或检索配置变化",
  uncacheable: "上次结果不可缓存", oversized: "上次结果超出容量",
  cancelled: "上次请求已取消", compute_error: "上次检索失败",
};

/** Mounted only inside the administrator's expanded tools. No chat-space cost. */
export default function KnowledgeCacheMetric() {
  const [stats, setStats] = useState<CacheStats | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  useEffect(() => {
    let disposed = false;
    let active: AbortController | null = null;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    async function refresh() {
      if (document.hidden || active) return;
      const controller = new AbortController(); active = controller;
      setLoading(true);
      deadline = setTimeout(() => controller.abort(), 10_000);
      try {
        const response = await fetch(`${API_BASE}/api/admin/knowledge/cache-stats`, {
          headers: apiHeaders(), cache: "no-store", signal: controller.signal,
        });
        checkAuthentication(response);
        if (!response.ok) throw new Error("unavailable");
        const data: CacheStats = await response.json();
        if (!disposed) { setStats(data); setFailed(false); }
      } catch {
        if (!disposed) setFailed(true);
      } finally {
        clearTimeout(deadline); active = null;
        if (!disposed) setLoading(false);
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 30_000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      disposed = true; clearInterval(timer); clearTimeout(deadline); active?.abort();
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [refreshKey]);

  const value = failed ? "暂不可用" : !stats ? "读取中" : !stats.enabled ? "已关闭"
    : stats.hit_rate === null ? "暂无数据" : `${(stats.hit_rate * 100).toFixed(1)}%`;
  const help = "有效命中 ÷ 参与缓存查询数；并发合并单独统计，不算命中。仅当前进程，重启后归零；每 30 秒刷新。";
  const reasons = Object.entries(stats?.miss_reasons ?? {}).filter(([, count]) => count > 0);
  const bypassed = Object.entries(stats?.diagnostics ?? {}).reduce((total, [key, count]) => total + (key.startsWith("bypass_") ? count : 0), 0);
  const withheld = Object.entries(stats?.diagnostics ?? {}).reduce((total, [key, count]) => total + (key.startsWith("withheld_") ? count : 0), 0);
  return <div className="px-3 py-1 text-xs text-ink-muted" data-testid="knowledge-cache-metric">
    <div className="flex min-h-9 items-center gap-1">
      <details className="min-w-0 flex-1">
        <summary title={help} className="cursor-pointer list-none rounded-md py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
          <span>缓存命中率</span><span className="ml-2 font-medium tabular-nums text-ink" aria-live="polite">{value}</span>
        </summary>
        <div data-testid="knowledge-cache-details" className="max-h-[min(60dvh,28rem)] overflow-y-auto pb-2 pr-2 leading-relaxed">
          <p>{help}</p>
          {!failed && stats && <div className="space-y-3">
            <p className="mt-1 tabular-nums">命中 {stats.hits} / 查询 {stats.requests} · 合并 {stats.coalesced}<br />缓存 {stats.entries} 条 · 统计自 {new Date(stats.started_at).toLocaleString("zh-CN")}</p>
            {stats.enabled && stats.requests > 0 && stats.hits === 0 && <p>尚无有效复用。新问题、上下文或聊天室变化都会重新检索，不代表没找到知识。</p>}
            {stats.ttl_seconds !== undefined && <p>保留 {Math.round(stats.ttl_seconds / 60)} 分钟 · 空结果 {stats.empty_ttl_seconds ?? 45} 秒<br />知识有更新即失效，无变化的例行检查保留缓存。</p>}
            {reasons.length > 0 && <div>
              <p className="font-medium text-ink">未命中原因</p>
              <dl className="mt-1 space-y-1 tabular-nums">{reasons.map(([reason, count]) => <div key={reason} className="flex items-start justify-between gap-3">
                <dt className="min-w-0">{reasonLabels[reason] ?? "其他"}</dt><dd className="shrink-0">{count}</dd>
              </div>)}</dl>
              <p className="mt-1">原因仅按短期指纹判断；重启或指纹淘汰后无法追溯。不同上下文按不同查询处理。</p>
            </div>}
            {stats.saved_model_calls !== undefined && <p className="tabular-nums">结果命中节省检索模型调用 {stats.saved_model_calls} 次<br />并发合并另节省 {stats.coalesced_saved_model_calls ?? 0} 次<br />无变化检查保留 {stats.diagnostics?.unchanged_index_refreshes ?? 0} 次 · 清空 {stats.invalidations ?? 0} 次</p>}
            {(bypassed > 0 || withheld > 0 || (stats.uncacheable ?? 0) > 0) && <p className="tabular-nums">绕过缓存 {bypassed} 次 · 安全拦截 {withheld} 次<br />异常结果未缓存 {stats.uncacheable ?? 0} 次</p>}
            {stats.public_preparation && <div>
              <p className="font-medium text-ink">公共目录准备复用</p>
              <p className="tabular-nums">{stats.public_preparation.hits} / {stats.public_preparation.requests} 次{stats.public_preparation.hit_rate !== null ? ` · ${(stats.public_preparation.hit_rate * 100).toFixed(1)}%` : " · 暂无数据"}</p>
              <p>只复用公共目录结构，仍按当前问题重新筛选。不计入上方命中率，也不省略模型审核。</p>
            </div>}
          </div>}
        </div>
      </details>
      <button type="button" aria-label="刷新缓存命中率" title="刷新缓存命中率" disabled={loading}
        onClick={() => setRefreshKey(key => key + 1)}
        className="surface-button flex h-9 w-9 shrink-0 items-center justify-center self-start rounded-lg text-ink-muted disabled:opacity-50">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="h-3.5 w-3.5"><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6.1 7a7 7 0 0 1 11.5-1L20 9M4 15l2.4 3A7 7 0 0 0 18 17" /></svg>
      </button>
    </div>
  </div>;
}
