"use client";

import { useEffect, useRef, useState } from "react";
import { createInvitations, listInvitations, type InvitationList } from "@/lib/invitations";

const button = "min-h-11 rounded-xl border border-line px-4 py-2 text-sm text-ink transition-colors hover:bg-raised disabled:cursor-not-allowed disabled:opacity-50";
const date = (value: string) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });

export default function InvitationManager() {
  const [data, setData] = useState<InvitationList>({ invitations: [], has_more: false });
  const [used, setUsed] = useState(false);
  const [offset, setOffset] = useState(0);
  const [count, setCount] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    listInvitations(used, offset, controller.signal).then((result) => { if (!controller.signal.aborted) setData(result); }).catch((err) => {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [used, offset, refresh]);

  async function generate() {
    if (savingRef.current) return;
    savingRef.current = true; setSaving(true); setError(null); setNotice("");
    try {
      const created = await createInvitations(count);
      setData(created); setUsed(false); setOffset(0); setRefresh((n) => n + 1);
      setNotice(`已生成 ${created.invitations.length} 个邀请码，每个仅可注册一次。`);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { savingRef.current = false; setSaving(false); }
  }

  async function copy(code: string) {
    try { await navigator.clipboard.writeText(code); setNotice("邀请码已复制，可以发送给受邀用户。"); }
    catch { setError("无法自动复制，请选中下方邀请码手动复制。"); }
  }

  return <div className="space-y-5">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div><h3 className="text-base font-medium text-ink">注册邀请码</h3>
        <p className="mt-1 text-xs leading-6 text-ink-muted">长期有效 · 每码一人 · 成功注册后不可再次使用</p></div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink-muted">数量</span>
          <div role="group" aria-label="生成邀请码数量" className="flex gap-1 rounded-2xl border border-line bg-raised p-1">
            {[1, 5, 10, 20].map((n) => <button key={n} type="button" aria-label={`${n} 个邀请码`} aria-pressed={count === n}
              disabled={saving} onClick={() => setCount(n)}
              className={`min-h-11 min-w-11 rounded-xl px-2 text-sm tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-edge disabled:opacity-50 ${count === n ? "bg-accent text-on-accent shadow-sm" : "text-ink-muted hover:bg-panel hover:text-ink"}`}>{n}</button>)}
          </div>
        </div>
        <button type="button" className={`${button} border-accent-edge bg-accent-wash text-accent-ink`} disabled={saving} onClick={() => void generate()}>
          {saving ? "正在生成…" : "生成邀请码"}
        </button>
      </div>
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className={`${button} ${!used ? "bg-accent-wash text-accent-ink" : ""}`} aria-pressed={!used} disabled={saving} onClick={() => { setUsed(false); setOffset(0); }}>未使用</button>
      <button type="button" className={`${button} ${used ? "bg-accent-wash text-accent-ink" : ""}`} aria-pressed={used} disabled={saving} onClick={() => { setUsed(true); setOffset(0); }}>已使用</button>
      <button type="button" className={`${button} ml-auto`} disabled={loading || saving} onClick={() => setRefresh((n) => n + 1)}>刷新</button>
    </div>
    {error && <p role="alert" className="rounded-xl bg-alert-wash p-3 text-sm text-alert-ink">{error}</p>}
    {notice && <p role="status" className="text-sm text-accent-ink">{notice}</p>}
    {loading ? <p aria-busy="true" className="py-8 text-center text-sm text-ink-muted">正在读取邀请码…</p> :
      data.invitations.length === 0 ? <p className="py-8 text-center text-sm text-ink-muted">{used ? "暂无已使用的邀请码" : "暂无可用邀请码，点击上方按钮生成。"}</p> :
      <ul className="divide-y divide-line rounded-2xl border border-line bg-raised">
        {data.invitations.map((invite) => <li key={invite.id} className="flex flex-wrap items-center gap-3 p-4">
          <div className="min-w-0 flex-1 basis-56">
            <code className="select-all break-all text-sm text-ink">{invite.code}</code>
            <p className="mt-2 text-xs leading-5 text-ink-muted">生成：{date(invite.created_at)}</p>
            {invite.used_at && <p className="text-xs leading-5 text-ink-muted">使用：{date(invite.used_at)} · {invite.used_by_username ?? "账号已删除"}</p>}
          </div>
          {invite.used_at ? <span className="text-xs text-ink-muted">已使用</span> :
            <button type="button" className={button} aria-label={`复制邀请码 ${invite.code}`} onClick={() => void copy(invite.code)}>复制</button>}
        </li>)}
      </ul>}
    {(offset > 0 || data.has_more) && <div className="flex items-center justify-between gap-2">
      <button type="button" className={button} disabled={offset === 0 || loading} onClick={() => setOffset((n) => Math.max(0, n - 50))}>上一页</button>
      <span className="text-xs text-ink-muted">第 {Math.floor(offset / 50) + 1} 页</span>
      <button type="button" className={button} disabled={!data.has_more || loading} onClick={() => setOffset((n) => n + 50)}>下一页</button>
    </div>}
  </div>;
}
