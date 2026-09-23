import { API_BASE } from "@/lib/api";
import { apiHeaders } from "@/lib/http";

export interface AdminAccountItem {
  id: number;
  username: string;
  nickname: string | null;
  tag: string | null;
  display_id: string | null;
  role: "user" | "admin";
  created_at: string;
  last_login_at: string | null;
}

async function detail(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  return typeof body?.detail === "string" ? body.detail : fallback;
}

export async function fetchAdminAccounts(
  query = "",
  signal?: AbortSignal,
): Promise<AdminAccountItem[]> {
  const params = new URLSearchParams();
  if (query.trim()) params.set("query", query.trim());
  const suffix = params.size ? `?${params.toString()}` : "";
  const res = await fetch(`${API_BASE}/api/admin/accounts${suffix}`, {
    headers: apiHeaders(),
    signal,
  });
  if (!res.ok) throw new Error(await detail(res, "无法读取账号列表"));
  const body = (await res.json()) as { accounts: AdminAccountItem[] };
  return body.accounts;
}

export async function grantAdminRole(accountId: number): Promise<AdminAccountItem> {
  const res = await fetch(`${API_BASE}/api/admin/accounts/${accountId}/role`, {
    method: "PATCH",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify({ role: "admin" }),
  });
  if (!res.ok) throw new Error(await detail(res, "授予管理员权限失败"));
  return res.json() as Promise<AdminAccountItem>;
}

export async function deleteAdminAccount(accountId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/accounts/${accountId}`, {
    method: "DELETE",
    headers: apiHeaders(),
  });
  if (!res.ok) throw new Error(await detail(res, "删除账号失败"));
}
