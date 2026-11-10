import { API_BASE } from "@/lib/api";
import { apiHeaders } from "@/lib/http";
import type { ConversationDetail } from "@/lib/conversations";
import type { MessageDebugDetails } from "@/components/KnowledgeReferenceDetails";
import type { CreatedConversationShare } from "@/lib/shares";

export interface AdminAccountItem {
  id: number;
  username: string;
  nickname: string | null;
  role: "user" | "admin";
  created_at: string;
  last_login_at: string | null;
  last_active_at?: string | null;
  research_id?: string | null;
  phone?: string | null;
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

export async function deleteAdminAccount(accountId: number, password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/accounts/${accountId}`, {
    method: "DELETE",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify({ password }),
  });
  if (!res.ok) throw new Error(await detail(res, "删除账号失败"));
}

export async function setResearchId(accountId: number, researchId: string | null): Promise<AdminAccountItem> {
  const res = await fetch(`${API_BASE}/api/admin/accounts/${accountId}/research-id`, {
    method: "PUT",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify({ research_id: researchId }),
  });
  if (!res.ok) throw new Error(await detail(res, res.status === 409 ? "这个研究编号已被其他账号使用" : "保存研究编号失败"));
  return res.json() as Promise<AdminAccountItem>;
}

/** Every chat of one account, oldest first, each with its full transcript. */
export async function fetchAccountConversations(
  accountId: number,
  signal?: AbortSignal,
): Promise<{ account: AdminAccountItem; conversations: ConversationDetail[] }> {
  const res = await fetch(`${API_BASE}/api/admin/accounts/${accountId}/conversations`, {
    headers: apiHeaders(),
    signal,
  });
  if (!res.ok) throw new Error(await detail(res, "加载聊天记录失败"));
  return res.json();
}

/** What the 调试详情 panels of one saved reply need beyond the transcript itself. */
export async function fetchAccountMessageDebug(
  accountId: number,
  messageId: number,
  signal?: AbortSignal,
): Promise<MessageDebugDetails> {
  const res = await fetch(`${API_BASE}/api/admin/accounts/${accountId}/messages/${messageId}/debug`, {
    headers: apiHeaders(),
    signal,
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await detail(res, "加载调试详情失败"));
  return res.json();
}

/**
 * Freeze a run of one chat's messages (both ends included), with their debug
 * details, behind a share link that anyone holding it can open without login.
 */
export async function createAccountShare(
  accountId: number,
  range: { sessionId: string; firstMessageId: number; lastMessageId: number },
): Promise<CreatedConversationShare> {
  const res = await fetch(`${API_BASE}/api/admin/accounts/${accountId}/shares`, {
    method: "POST",
    headers: apiHeaders({ json: true }),
    cache: "no-store",
    body: JSON.stringify({
      session_id: range.sessionId,
      first_message_id: range.firstMessageId,
      last_message_id: range.lastMessageId,
    }),
  });
  if (!res.ok) throw new Error(await detail(res, "创建分享链接失败，请重试。"));
  return res.json() as Promise<CreatedConversationShare>;
}
