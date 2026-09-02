"use client";

import { useEffect, useState } from "react";
import {
  fetchAdminAccounts,
  grantAdminRole,
  type AdminAccountItem,
} from "@/lib/adminAccounts";
import { CloseMark, UsersMark } from "@/components/icons";
import ConfirmDialog from "@/components/ConfirmDialog";

function formatDate(value: string | null): string {
  if (!value) return "尚未登录";
  return new Date(value).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function AdminAccountManagerModal({
  currentUsername,
  onClose,
}: {
  currentUsername: string;
  onClose: () => void;
}) {
  const [accounts, setAccounts] = useState<AdminAccountItem[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [grantingId, setGrantingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [grantTarget, setGrantTarget] = useState<AdminAccountItem | null>(null);

  async function load(search = query, signal?: AbortSignal) {
    setLoading(true);
    setError(null);
    try {
      setAccounts(await fetchAdminAccounts(search, signal));
    } catch (err) {
      if ((err as { name?: string })?.name !== "AbortError") {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load("", controller.signal);
    return () => controller.abort();
    // Initial directory load only. Search is explicit to avoid issuing a
    // request for every IME keystroke while a Chinese name is being composed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !grantTarget) onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [grantTarget, onClose]);

  async function handleGrant(account: AdminAccountItem) {
    setGrantTarget(account);
  }

  async function performGrant(account: AdminAccountItem) {
    setGrantTarget(null);
    setGrantingId(account.id);
    setError(null);
    try {
      const updated = await grantAdminRole(account.id);
      setAccounts((prev) =>
        prev.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setGrantingId(null);
    }
  }

  return (
    <div className="zen-overlay-enter fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-5">
      <button
        type="button"
        aria-label="关闭账号管理"
        onClick={onClose}
        className="absolute inset-0 bg-canvas/75 backdrop-blur-md"
      />

      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="account-manager-title"
        className="relative flex h-[min(780px,calc(100dvh-1.5rem))] min-h-0 w-full max-w-4xl flex-col overflow-hidden rounded-[28px] border border-line bg-panel depth-panel backdrop-blur-2xl sm:h-[min(780px,calc(100dvh-2.5rem))]"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-line px-5 py-4 sm:px-6">
          <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-accent-edge bg-accent-wash text-accent-ink">
            <UsersMark className="h-4.5 w-4.5" />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 id="account-manager-title" className="text-base font-medium text-ink">
                管理员 · 账号与权限管理
              </h2>
              <span className="rounded-full bg-accent-wash px-2 py-0.5 text-[0.62rem] tracking-[0.12em] text-accent-ink">
                ADMIN
              </span>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-ink-faint">
              管理员可在此查看账号，并为现有用户授予管理员权限。登录账号独立且唯一；公开身份使用昵称与用户自选五位数字标签。
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="ml-auto grid h-9 w-9 shrink-0 place-items-center rounded-full text-ink-muted transition-colors duration-300 hover:bg-raised hover:text-ink"
          >
            <CloseMark className="h-4 w-4" />
          </button>
        </header>

        <form
          className="flex shrink-0 gap-2 border-b border-line px-4 py-3 sm:px-6"
          onSubmit={(event) => {
            event.preventDefault();
            void load();
          }}
        >
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索登录账号、昵称或完整 昵称#12345"
            aria-label="搜索账号"
            className="min-w-0 flex-1 rounded-2xl border border-line bg-canvas/45 px-4 py-2.5 text-sm text-ink outline-none transition-colors duration-300 placeholder:text-ink-faint focus:border-accent-edge focus:bg-canvas/65"
          />
          <button
            type="submit"
            disabled={loading}
            className="shrink-0 rounded-2xl border border-accent-edge bg-accent-wash px-4 py-2.5 text-sm text-accent-ink transition-colors duration-300 hover:bg-raised disabled:cursor-wait disabled:opacity-60"
          >
            搜索
          </button>
        </form>

        {error && (
          <p className="mx-4 mt-3 shrink-0 rounded-xl border border-alert-edge bg-alert-wash px-3 py-2 text-xs text-alert-ink sm:mx-6">
            {error}
          </p>
        )}

        <div className="zen-scroll min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
          {loading ? (
            <div className="grid h-full min-h-32 place-items-center" aria-busy="true">
              <span className="h-7 w-7 animate-spin rounded-full border-2 border-line-strong border-t-accent" />
            </div>
          ) : accounts.length === 0 ? (
            <div className="grid h-full min-h-32 place-items-center text-sm text-ink-faint">
              没有找到符合条件的账号
            </div>
          ) : (
            <div className="space-y-3">
              {accounts.map((account) => {
                const isCurrent = account.username === currentUsername;
                return (
                  <article
                    key={account.id}
                    className="flex flex-col gap-3 rounded-2xl border border-line bg-raised p-4 sm:flex-row sm:items-center"
                  >
                    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-line bg-panel text-ink-muted">
                      <UsersMark className="h-4.5 w-4.5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="break-all text-sm font-medium text-ink">
                          {account.display_id ?? account.nickname ?? "未设置公开昵称"}
                        </h3>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[0.62rem] tracking-[0.08em] ${
                            account.role === "admin"
                              ? "bg-accent-wash text-accent-ink"
                              : "bg-panel text-ink-faint"
                          }`}
                        >
                          {account.role === "admin" ? "ADMIN" : "USER"}
                        </span>
                        {isCurrent && (
                          <span className="text-[0.68rem] text-ink-faint">当前账号</span>
                        )}
                      </div>
                      <p className="mt-1 font-mono text-xs text-ink-muted">
                        登录账号：{account.username}
                      </p>
                      <p className="mt-1 text-[0.68rem] text-ink-faint">
                        注册：{formatDate(account.created_at)} · 最近登录：{formatDate(account.last_login_at)}
                      </p>
                    </div>
                    {account.role === "user" && (
                      <button
                        type="button"
                        disabled={grantingId !== null}
                        onClick={() => void handleGrant(account)}
                        className="shrink-0 rounded-xl border border-accent-edge bg-accent-wash px-3.5 py-2 text-xs text-accent-ink transition-all duration-300 hover:bg-panel disabled:cursor-wait disabled:opacity-50"
                      >
                        {grantingId === account.id ? "正在授予…" : "授予管理员"}
                      </button>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </div>

        <footer className="shrink-0 border-t border-line px-5 py-3 text-[0.68rem] leading-relaxed text-ink-faint sm:px-6">
          权限授予会立即生效；此页面不提供降级操作，以避免误触移除管理员权限。
        </footer>
      </section>

      <ConfirmDialog
        open={grantTarget !== null}
        title="授予管理员权限？"
        description={`${grantTarget?.display_id ?? grantTarget?.username ?? "该账号"} 将可以管理所有账号与全局共享提示词。权限授予后会立即生效。`}
        confirmLabel="授予管理员权限"
        cancelLabel="暂不授予"
        busy={grantingId !== null}
        onCancel={() => setGrantTarget(null)}
        onConfirm={() => {
          if (grantTarget) void performGrant(grantTarget);
        }}
      />
    </div>
  );
}
